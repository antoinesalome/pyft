"""
This module implements the Applications class containing methods
for high-to-moderate level transformation
"""

import copy
import os

from pyft.util import debugDecor, alltext, n2name, isStmt, PYFTError, tag, noParallel
from pyft.expressions import createExpr, createExprPart, createElem, simplifyExpr
from pyft.tree import updateTree
from pyft.variables import updateVarList
from pyft import NAMESPACE


def _loopVarPHYEX(lowerDecl, upperDecl, lowerUsed, upperUsed, name, i):
    """
    Try to guess the name of the variable to use for looping on indexes
    :param lowerDecl, upperDecl: lower and upper bounds as defined in the declaration statement
    :param lowerUsed, upperUsed: lower and upper bounds as given in the statement
    :param name: name of the array
    :param i: index of the rank
    :return: the variable name of False to discard this statement
    """
    if lowerUsed is not None and lowerUsed.upper() == 'IIJB' and \
       upperUsed is not None and upperUsed.upper() == 'IIJE':
        varName = 'JIJ'
    elif upperDecl is None or lowerDecl is None:
        varName = False
    elif upperDecl.upper() in ('KSIZE', 'KPROMA', 'KMICRO',
                               'IGRIM', 'IGACC', 'IGDRY', 'IGWET'):
        varName = 'JL'
    elif upperDecl.upper() in ('D%NIJT', 'IIJE') or lowerDecl.upper() in ('D%NIJT', 'IIJB') or \
            'D%NIJT' in upperDecl.upper() + lowerDecl.upper():
        # REAL, DIMENSION(MERGE(D%NIJT, 0, PARAMI%LDEPOSC)), INTENT(OUT) :: PINDEP
        varName = 'JIJ'
    elif upperDecl.upper() in ('IKB', 'IKE', 'IKT', 'D%NKT', 'KT') or \
            'D%NKT' in upperDecl.upper():
        # REAL, DIMENSION(MERGE(D%NIJT, 0, OCOMPUTE_SRC),
        #                 MERGE(D%NKT, 0, OCOMPUTE_SRC)), INTENT(OUT) :: PSIGS
        varName = 'JK'
    elif upperDecl.upper() == 'KSV' or lowerDecl.upper() == 'KSV':
        varName = 'JSV'
    elif upperDecl.upper() == 'KRR':
        varName = 'JRR'
    elif upperDecl.upper() in ('D%NIT', 'IIE', 'IIU') or lowerDecl.upper() == 'IIB' or \
            'D%NIT' in upperDecl.upper():
        varName = 'JI'
    elif upperDecl.upper() in ('D%NJT', 'IJE', 'IJU') or lowerDecl.upper() == 'IJB' or \
            'D%NJT' in upperDecl.upper():
        varName = 'JJ'
    else:
        varName = False
    return varName


class Applications():
    """
    Methods for high-to-moderate level transformation
    """

    @debugDecor
    def deleteNonColumnCallsPHYEX(self, simplify=False):
        """
        Remove PHYEX routines that compute with different vertical columns not needed for AROME
        MODE_ROTATE_WIND, UPDATE_ROTATE_WIND
        If Simplify is True, also remove all variables only needed for these calls
        :param simplify : if True, remove variables that are now unused
        """
        for subroutine in ('ROTATE_WIND', 'UPDATE_ROTATE_WIND', 'BL_DEPTH_DIAG_3D',
                           'TM06_H', 'TURB_HOR_SPLT'):
            # Remove call statements
            nb = self.removeCall(subroutine, None, simplify=simplify)
            # Remove use statement
            if nb > 0:
                self.removeVar([(v['scopePath'], v['n']) for v in self.varList
                                if v['n'] == subroutine], simplify=simplify)

    @debugDecor
    def convertTypesInCompute(self):
        """
        Convert STR%VAR into single local variable contained in compute (a-stmt)
        and in if-then-stmt, else-if-stmt, where-stmt
        e.g.
        ZA = 1 + CST%XG ==> ZA = 1 + XCST_G
        ZA = 1 + PARAM_ICE%XRTMIN(3)  ==> ZA = 1 + XPARAM_ICE_XRTMIN3
        ZRSMIN(1:KRR) = ICED%XRTMIN(1:KRR) => ZRSMIN(1:KRR) = ICEDXRTMIN1KRR(1:KRR)
        IF(TURBN%CSUBG_MF_PDF=='NONE')THEN => IF(CTURBNSUBG_MF_PDF=='NONE')THEN

        RESTRICTION : works only if the r-component variable is contained in 1 parent structure.
        Allowed for conversion : CST%XG
        Not converted : TOTO%CST%XG (for now, recursion must be coded)
        Not converted : TOTO%ARRAY(:) (shape of the array must be determined from E1)
        """
        def convertOneType(component, newVarList):
            # 1) Build the name of the new variable
            objType = self.getParent(component, 2)  # The object STR%VAR
            objTypeStr = alltext(objType)
            namedENn = objType.find('.//{*}N/{*}n')
            structure = namedENn.text
            variable = component.find('.//{*}ct').text
            # If the variable is an array with index selection
            # such as ICED%XRTMIN(1:KRR)
            arrayIndices = ''
            arrayRall = objType.findall('.//{*}array-R')
            if len(arrayRall) > 0:
                arrayR = copy.deepcopy(arrayRall[0])  # Save for the declaration
                txt = alltext(arrayR).replace(',', '')
                txt = txt.replace(':', '')
                txt = txt.replace('(', '')
                txt = txt.replace(')', '')
                arrayIndices = arrayIndices + txt
            elif len(objType.findall('.//{*}element-LT')) > 0:
                # Case with single element such as ICED%XRTMIN(1)
                for elem in objType.findall('.//{*}element'):
                    arrayIndices = arrayIndices + alltext(elem)
            newName = variable[0] + structure + variable[1:] + arrayIndices
            
            # 2) Replace the namedE>N>n by the newName and delete R-LT 
            #except for array with index selection (R-LT is moved)
            namedENn.text = newName
            objType.remove(objType.find('.//{*}R-LT'))
            if len(arrayRall) > 0:
                objType.insert(1,arrayR)
            
                
            # 3) Add to the list of not already present for declaration
            if newName not in newVarList:
                if len(arrayRall) == 0:
                    newVarList[newName] = (None, objTypeStr)
                else:
                    newVarList[newName] = (arrayR, objTypeStr)
                    
        scopes = self.getScopes(excludeContains=True)
        if scopes[0].path.split('/')[-1].split(':')[1][:4] == 'MODD':
            return
        for scope in [scope for scope in scopes
                      if 'sub:' in scope.path and 'interface' not in scope.path]:
            print(scope.path)
            newVarList = {}
            for ifStmt in scope.findall('.//{*}if-then-stmt') + scope.findall('.//{*}else-if-stmt') \
                + scope.findall('.//{*}where-stmt'): 
                compo = ifStmt.findall('.//{*}component-R')
                if len(compo) > 0:
                    for elcompo in compo:
                        convertOneType(elcompo, newVarList)
                
            for aStmt in scope.findall('.//{*}a-stmt'):
                # Exclude statements in which the component-R is in E1
                # (e.g. PARAMI%XRTMIN(4) = 2)
                if len(aStmt[0].findall('.//{*}component-R')) == 0:  # E1 is the first son of aStmt
                    compoE2 = aStmt.findall('.//{*}component-R')
                    if len(compoE2) > 0:
                        # Exclude stmt from which E2 has only 1 named-E/{*}N/{*}n e.g. IKB = D%NKB
                        #  warning, it does not handle yet op in simple statement
                        #  such as ZEXPL = 1.- TURBN%XIMPL
                        # Include stmt from which E2 has 1 named-E/{*}N/{*}n AND E1 is an array;
                        # e.g. ZDELTVPT(JIJ,JK)=CSTURB%XLINF
                        nbNamedEinE2 = len(aStmt.findall('.//{*}E-2')[0].findall('.//{*}named-E/' +
                                                                                 '{*}N/{*}n'))
                        if nbNamedEinE2 > 1 or nbNamedEinE2 == 1 and \
                           len(aStmt[0].findall('.//{*}R-LT')) == 1:
                            for elcompoE2 in compoE2:
                                convertOneType(elcompoE2, newVarList)

            # Add the declaration of the new variables and their affectation
            for el, var in newVarList.items():
                if el[0].upper() == 'X' or el[0].upper() == 'P' or el[0].upper() == 'Z':
                    varType = 'REAL'
                elif el[0].upper() == 'L' or el[0].upper() == 'O':
                    varType = 'LOGICAL'
                elif el[0].upper() == 'N' or el[0].upper() == 'I' or el[0].upper() == 'K':
                    varType = 'INTEGER'
                elif el[0].upper() == 'C':
                    varType = 'CHARACTER(LEN=LEN(' + var[1] + '))'
                else:
                    raise PYFTError('Case not implemented for the first letter of the newVarName' +
                                    el + ' in convertTypesInCompute')
                varArray = ''
                # Handle the case the variable is an array
                if var[0]:
                    varArray = ', DIMENSION('
                    for i, sub in enumerate(var[0].findall('.//{*}section-subscript')):
                        if len(sub.findall('.//{*}upper-bound')) > 0:
                            dimSize = simplifyExpr(
                                          alltext(sub.findall('.//{*}upper-bound')[0]) +
                                          '-' + alltext(sub.findall('.//{*}lower-bound')[0]) +
                                          ' + 1')
                        elif len(sub.findall('.//{*}lover-bound')) > 0:
                            dimSize = simplifyExpr(alltext(sub.findall('.//{*}lower-bound')[0]))
                        else:  # Case XRTMIN(:)
                            dimSize = 'SIZE(' + var[1] + ',' + str(i+1) + ')'
                        varArray = ', DIMENSION(' + dimSize + ','
                    varArray = varArray[:-1] + ')'
                self.addVar([[scope.path, el, varType + varArray + ' :: ' + el, None]])

                # Affectation
                stmtAffect = createExpr(el + "=" + var[1])[0]
                self.insertStatement(scope.path, self.indent(stmtAffect), first=True)

    @debugDecor
    def deleteDrHook(self, simplify=False):
        """
        Remove DR_HOOK calls.
        If Simplify is True, also remove all variables only needed for these calls (ZHOOK_HANDLE,
        DR_HOOK, LHOOK, YOMHOOK, JPRB, PARKIND1)
        :param simplify : if True, remove variables that are now unused
        """
        self.removeCall('DR_HOOK', None, simplify=simplify)

    @debugDecor
    def addDrHook(self):
        """
        Add DR_HOOK calls.
        """
        for scope in [scope for scope in self.getScopes(excludeContains=True)
                      if scope.path.split('/')[-1].split(':')[0] in ('func', 'sub') and
                      (len(scope.path.split('/')) == 1 or
                       scope.path.split('/')[-2].split(':')[0] != 'interface')]:
            name = scope.path.split(':')[-1].upper()
            # Add USE YOMHOOK,    ONLY: LHOOK, DR_HOOK, JPHOOK
            self.addModuleVar([[scope.path, 'YOMHOOK', ['LHOOK', 'DR_HOOK', 'JPHOOK']]])
            # REAL(KIND=JPHOOK) :: ZHOOK_HANDLE
            self.addVar([[scope.path, 'ZHOOK_HANDLE', 'REAL(KIND=JPHOOK) :: ZHOOK_HANDLE',
                          None]])
            # Insert IF (LHOOK) CALL DR_HOOK('XXnameXX', 0, ZHOOK_HANDLE)
            self.insertStatement(scope.path,
                                 createExpr(f"IF (LHOOK) CALL DR_HOOK('{name}', " +
                                            "0, ZHOOK_HANDLE)")[0], True)
            # Insert IF (LHOOK) CALL DR_HOOK('XXnameXX', 1, ZHOOK_HANDLE)
            self.insertStatement(scope.path,
                                 createExpr(f"IF (LHOOK) CALL DR_HOOK('{name}', " +
                                            "1, ZHOOK_HANDLE)")[0], False)

    @debugDecor
    def deleteBudgetDDH(self, simplify=False):
        """
        Remove Budget calls.
        If Simplify is True, also remove all variables only needed for these calls
        :param simplify : if True, remove variables that are now unused
        """
        self.removeCall('BUDGET_STORE_INIT_PHY', None, simplify=simplify)
        self.removeCall('BUDGET_STORE_END_PHY', None, simplify=simplify)
        self.removeCall('BUDGET_STORE_ADD_PHY', None, simplify=simplify)
        flagTorm = ['BUCONF%LBUDGET_SV', 'BUCONF%LBUDGET_TKE', 'BUCONF%LBUDGET_TH',
                    'BUCONF%LBUDGET_RI', 'BUCONF%LBUDGET_RV', 'BUCONF%LBUDGET_RG',
                    'BUCONF%LBUDGET_RS', 'BUCONF%LBUDGET_RH', 'BUCONF%LBUDGET_RR',
                    'BUCONF%LBUDGET_RC', 'BUCONF%LBUDGET_U', 'BUCONF%LBUDGET_V',
                    'BUCONF%LBUDGET_W']
        self.setFalseIfStmt(flagTorm, None, simplify=simplify)

    @debugDecor
    def addMPPDB_CHECKS(self):

        """
        Add MPPDB_CHEKS on all intent REAL arrays on subroutines.
        ****** Not applied on modd_ routines. ********
        Handle optional arguments.
        Example, for a BL89 routine with 4 arguments, 1 INTENT(IN),
                 2 INTENT(INOUT), 1 INTENT(OUT), it produces :
        IF (MPPDB_INITIALIZED) THEN
          !Check all IN arrays
          CALL MPPDB_CHECK(PZZ, "BL89 beg:PZZ")
          !Check all INOUT arrays
          CALL MPPDB_CHECK(PDZZ, "BL89 beg:PDZZ")
          CALL MPPDB_CHECK(PTHVREF, "BL89 beg:PTHVREF")
        END IF
        ...
        IF (MPPDB_INITIALIZED) THEN
          !Check all INOUT arrays
          CALL MPPDB_CHECK(PDZZ, "BL89 end:PDZZ")
          CALL MPPDB_CHECK(PTHVREF, "BL89 end:PTHVREF")
          !Check all OUT arrays
          CALL MPPDB_CHECK(PLM, "BL89 end:PLM")
        END IF
        """
        def addMPPDB_CHECK_statement(var, subRoutineName, strMSG='beg:'):
            ifBeg, ifEnd, addD, addLastDim, addSecondDimType = '', '', '', '', ''
            # Test if the variable is declared with the PHYEX D% structure,
            # in that case, use the PHYEX MPPDB_CHECK interface
            if var['as'][0][1]:  # If not NoneType
                if 'D%NIJT' in var['as'][0][1]:
                    addD = 'D,'
                    if len(var['as']) == 2:
                        # This handle 2D arrays with the last dim either D%NKT or anything else.
                        addLastDim = ', ' + var['as'][1][1]
                    if len(var['as']) >= 2:
                        # This adds information on the type of the second dimension :
                        # is it the vertical one or not, to remove extra points
                        if 'D%NK' in var['as'][1][1]:
                            addSecondDimType = ',' + '''"VERTICAL"'''
                        else:
                            addSecondDimType = ',' + '''"OTHER"'''
                if 'MERGE' in var['as'][-1][1]:  # e.g. MERGE(D%NKT,0,OCLOUDMODIFLM)
                    keyDimMerge = var['as'][-1][1].split(',')[2][:-1]  # e.g. OCLOUDMODIFLM
                    ifBeg = 'IF (' + keyDimMerge + ') THEN\n'
                    ifEnd = '\nEND IF\n'
            if var['opt']:
                ifBeg = ifBeg + 'IF (PRESENT(' + var['n'] + ')) THEN\n IF (SIZE(' + \
                        var['n'] + ',1) > 0) THEN\n'
                ifEnd = ifEnd + '\nEND IF\nEND IF'
            argsMPPDB = var['n'] + ", " + "\"" + subRoutineName + " " + strMSG+var['n'] + "\""
            return createExpr(ifBeg + "CALL MPPDB_CHECK(" + addD + argsMPPDB +
                              addLastDim + addSecondDimType + ")" + ifEnd)[0]
        scopes = self.getScopes()
        if scopes[0].path.split('/')[-1].split(':')[1][:4] == 'MODD':
            return
        for scope in scopes:
            # Do not add MPPDB_CHEKS to :
            # - MODULE or FUNCTION object,
            # - interface subroutine from a MODI
            # but only to SUBROUTINES
            if 'sub:' in scope.path and 'func' not in scope.path and 'interface' not in scope.path:
                subRoutineName = scope.path.split('/')[-1].split(':')[1]

                # Look for all intent arrays only
                arraysIn, arraysInOut, arraysOut = [], [], []
                for var in scope.varList:
                    if var['arg'] and var['as'] and 'TYPE' not in var['t'] and \
                       'REAL' in var['t'] and var['scopePath'] == scope.path:
                        if var['i'] == 'IN':
                            arraysIn.append(var)
                        if var['i'] == 'INOUT':
                            arraysInOut.append(var)
                        if var['i'] == 'OUT':
                            arraysOut.append(var)
                # Check if there is any intent variables
                if len(arraysIn) + len(arraysInOut) + len(arraysOut) == 0:
                    break

                # Add necessary module
                self.addModuleVar([(scope.path, 'MODE_MPPDB', None)])

                # Prepare some FORTRAN comments
                commentIN = createElem('C', text='!Check all IN arrays', tail='\n')
                commentINOUT = createElem('C', text='!Check all INOUT arrays', tail='\n')
                commentOUT = createElem('C', text='!Check all OUT arrays', tail='\n')

                # 1) variables IN and INOUT block (beggining of the routine)
                if len(arraysIn) + len(arraysInOut) > 0:
                    ifMPPDBinit = createExpr("IF (MPPDB_INITIALIZED) THEN\n  END IF")[0]
                    ifMPPDB = ifMPPDBinit.find('.//{*}if-block')

                    # Variables IN
                    if len(arraysIn) > 0:
                        ifMPPDB.insert(1, commentIN)
                        for i, var in enumerate(arraysIn):
                            ifMPPDB.insert(2 + i, addMPPDB_CHECK_statement(var, subRoutineName,
                                                                           strMSG='beg:'))

                    # Variables INOUT
                    if len(arraysInOut) > 0:
                        shiftLineNumber = 2 if len(arraysIn) > 0 else 1
                        ifMPPDB.insert(len(arraysIn) + shiftLineNumber, commentINOUT)
                        for i, var in enumerate(arraysInOut):
                            ifMPPDB.insert(len(arraysIn) + shiftLineNumber + 1 + i,
                                           addMPPDB_CHECK_statement(var, subRoutineName,
                                                                    strMSG='beg:'))

                    # Add the new IN and INOUT block
                    self.insertStatement(scope.path, self.indent(ifMPPDBinit), first=True)

                # 2) variables INOUT and OUT block (end of the routine)
                if len(arraysInOut) + len(arraysOut) > 0:
                    ifMPPDBend = createExpr("IF (MPPDB_INITIALIZED) THEN\n  END IF")[0]
                    ifMPPDB = ifMPPDBend.find('.//{*}if-block')

                    # Variables INOUT
                    if len(arraysInOut) > 0:
                        ifMPPDB.insert(1, commentINOUT)
                        for i, var in enumerate(arraysInOut):
                            ifMPPDB.insert(2 + i, addMPPDB_CHECK_statement(var, subRoutineName,
                                                                           strMSG='end:'))

                    # Variables OUT
                    if len(arraysOut) > 0:
                        shiftLineNumber = 2 if len(arraysInOut) > 0 else 1
                        ifMPPDB.insert(len(arraysInOut) + shiftLineNumber, commentOUT)
                        for i, var in enumerate(arraysOut):
                            ifMPPDB.insert(len(arraysInOut) + shiftLineNumber + 1 + i,
                                           addMPPDB_CHECK_statement(var, subRoutineName,
                                                                    strMSG='end:'))

                    # Add the new INOUT and OUT block
                    self.insertStatement(scope.path, self.indent(ifMPPDBend), first=False)

    @debugDecor
    def addStack(self, model, stopScopes, parser=None, parserOptions=None, wrapH=False):
        """
        Add specific allocations of local arrays on the fly for GPU
        :param model : 'MESONH' or 'AROME' for specific objects related to the allocator or stack
        :param stopScopes: scope paths where we stop to add stack
        :param parser, parserOptions, wrapH: see the pyft class

        Stacks are added to all routines called by the scopes listed in stopScopes
        """
        if model == 'AROME':
            # The AROME transformation needs an additional parameter
            # We apply the transformation only if the routine is called
            # from a scope within stopScopes
            for scope in [scope for scope in self.getScopes()
                          if scope.path in stopScopes or
                          self.tree.isUnderStopScopes(scope.path, stopScopes)]:
                # Intermediate transformation, needs cpp to be completed
                # This version would be OK if we didn't need to read again the files with fxtran
                # after transformation
                # nb = self.modifyAutomaticArrays(
                #                            declTemplate="temp({type}, {name}, ({shape}))",
                #                            startTemplate="alloc({name})",
                #                            scopePath=scope.path)

                # Full transformation, using CRAY pointers
                # In comparison with the original transformation of Philippe,
                # we do not call SOF with __FILE__ and __LINE__ because it breaks
                # future reading with fxtran
                nb = self.modifyAutomaticArrays(
                         declTemplate="{type}, DIMENSION({shape}) :: {name}; " +
                                      "POINTER(IP_{name}_, {name})",
                         startTemplate="IP_{name}_=YLSTACK%L(KIND({name})/4);" +
                                       "YLSTACK%L(KIND({name})/4)=" +
                                       "YLSTACK%L(KIND({name})/4)+" +
                                       "KIND({name})*SIZE({name});" +
                                       "IF(YLSTACK%L(KIND({name})/4)>" +
                                       "YLSTACK%U(KIND({name})/4))" +
                                       "CALL SOF('" + self.getFileName() + ":{name}', " +
                                       "KIND({name}))",
                         scopePath=scope.path)

                if nb > 0:
                    # Some automatic arrays have been modified,
                    # we need to add an argument to the routine
                    self.addArgInTree(scope.path, 'YDSTACK', 'TYPE (STACK) :: YDSTACK', -1,
                                      stopScopes, moduleVarList=[('STACK_MOD', ['STACK', 'SOF'])],
                                      otherNames=['YLSTACK'],
                                      parser=parser, parserOptions=parserOptions, wrapH=wrapH)

                    # Copy the stack to a local variable and use it for call statements
                    # this operation must be done after the call to addArgInTree
                    self.addVar([[scope.path, 'YLSTACK', 'TYPE (STACK) :: YLSTACK', None]])
                    self.insertStatement(scope, createExpr('YLSTACK=YDSTACK')[0], True)
                    for argN in scope.findall('.//{*}call-stmt/{*}arg-spec/' +
                                              '{*}arg/{*}arg-N/../{*}named-E/{*}N'):
                        if n2name(argN) == 'YDSTACK':
                            argN[0].text = 'YLSTACK'

        elif model == 'MESONH':
            for scope in self.getScopes():
                # We apply the transformation only if the routine is called
                # from a scope within stopScopes
                if (not self.tree.isValid) or stopScopes is None or scope.path in stopScopes or \
                   self.tree.isUnderStopScopes(scope.path, stopScopes):
                    nb = self.modifyAutomaticArrays(
                                declTemplate="{type}, DIMENSION({doubledotshape}), " +
                                             "POINTER, CONTIGUOUS :: {name}",
                                startTemplate="CALL MNH_MEM_GET({name}, {lowUpList})",
                                scopePath=scope.path)
                    if nb > 0:
                        # Some automatic arrays have been modified
                        # we need to add the stack module,
                        self.addModuleVar([(scope.path, 'MODE_MNH_ZWORK',
                                          ['MNH_MEM_GET', 'MNH_MEM_POSITION_PIN',
                                           'MNH_MEM_RELEASE'])])
                        # to pin the memory position,
                        self.insertStatement(
                            scope.path,
                            createExpr(f"CALL MNH_MEM_POSITION_PIN('{scope.path}')")[0], True)
                        # and to realease the memory
                        self.insertStatement(
                            scope.path,
                            createExpr(f"CALL MNH_MEM_RELEASE('{scope.path}')")[0], False)
        else:
            raise PYFTError('Stack is implemented only for AROME and MESONH models')

    @debugDecor
    def inlineContainedSubroutinesPHYEX(self, simplify=False):
        """
        Inline all contained subroutines in the main subroutine
        Steps :
            - Identify contained subroutines
            - Look for all CALL statements, check if it is a containted routines; if yes, inline
            - Delete the containted routines
        :param simplify: try to simplify code (construct or variables becoming useless)
        :param loopVar: None to create new variable for each added DO loop
                        (around ELEMENTAL subroutine calls)
                        or a function that return the name of the variable to use for
                        the loop control. This function returns a string (name of the variable),
                        or True to create a new variable, or False to not transform this statement
                        The functions takes as arguments:
                          - lower and upper bounds as defined in the declaration statement
                          - lower and upper bounds as given in the statement
                          - name of the array
                          - index of the rank
        """
        return self.inlineContainedSubroutines(simplify=simplify, loopVar=_loopVarPHYEX)

    @debugDecor
    @updateVarList
    def removeIJDim(self, stopScopes, parser=None, parserOptions=None, wrapH=False, simplify=False):
        """
        Transform routines to be called in a loop on columns
        :param stopScopes: scope paths where we stop to add the D argument (if needed)
        :param parser, parserOptions, wrapH: see the pyft class
        :param simplify: try to simplify code (remove useless dimensions in call)

        ComputeInSingleColumn :
        - Remove all Do loops on JI and JJ
        - Initialize former indexes JI, JJ, JIJ to first array element:
          JI=D%NIB, JJ=D%NJB, JIJ=D%NIJB
        - If simplify is True, replace (:,*) on I/J/IJ dimension on argument
          with explicit (:,*) on CALL statements:
            e.g. CALL FOO(D, A(:,JK,1), B(:,:))
                  ==> CALL FOO(D, A(JIJ,JK,1), B(:,:)) only if the target argument is not an array
        """

        indexToCheck = {'JI': ('D%NIB', 'D%NIT'),
                        'JJ': ('D%NJB', 'D%NJT'),
                        'JIJ': ('D%NIJB', 'D%NIJT')}

        def slice2index(namedE, scopePath):
            """
            Transform a slice on the horizontal dimension into an index
            Eg.: X(1:D%NIJT, 1:D%NKT) => X(JIJ, 1:D%NKT) Be careful, this array is not contiguous.
                 X(1:D%NIJT, JK) => X(JIJ, JK)
            :param namedE: array to transform
            :param scopePath: scope path where the array is
            """
            # Loop on all array dimensions
            for isub, sub in enumerate(namedE.findall('./{*}R-LT/{*}array-R/' +
                                                      '{*}section-subscript-LT/' +
                                                      '{*}section-subscript')):
                if ':' in alltext(sub):
                    loopIndex, _, _ = self.findIndexArrayBounds(namedE, isub,
                                                                scopePath, _loopVarPHYEX)
                    if loopIndex in indexToCheck:  # To be transformed
                        if sub.text == ':':
                            sub.text = None
                            lowerBound = createElem('lower-bound')
                            sub.insert(0, lowerBound)
                        else:
                            lowerBound = sub.find('./{*}lower-bound')
                            lowerBound.tail = ''
                            for item in lowerBound:
                                lowerBound.remove(item)
                        upperBound = sub.find('./{*}upper-bound')
                        if upperBound is not None:
                            sub.remove(upperBound)
                        lowerBound.append(createExprPart(loopIndex))
                        if loopIndex not in indexRemoved:
                            indexRemoved.append(loopIndex)
            # Transform array-R/section-subscript-LT/section-subscript
            #      into parens-R>/element-LT/element if needed
            if ':' not in alltext(namedE.find('./{*}R-LT/{*}array-R/{*}section-subscript-LT')):
                namedE.find('./{*}R-LT/{*}array-R').tag = f'{{{NAMESPACE}}}parens-R'
                namedE.find('./{*}R-LT/{*}parens-R/' +
                            '{*}section-subscript-LT').tag = f'{{{NAMESPACE}}}element-LT'
                for ss in namedE.findall('./{*}R-LT/{*}parens-R/' +
                                         '{*}element-LT/{*}section-subscript'):
                    ss.tag = f'{{{NAMESPACE}}}element'
                    lowerBound = ss.find('./{*}lower-bound')
                    for item in lowerBound:
                        ss.append(item)
                    ss.remove(lowerBound)

        # 0 - Preparation
        self.addArrayParentheses()
        self.expandAllArraysPHYEX()
        if simplify:
            self.attachArraySpecToEntity()
        hUupperBounds = [v[1] for v in indexToCheck.values()]  # Upper bounds for horizontal dim

        # Loop on all scopes (reversed order); except functions (in particular
        # FWSED from ice4_sedimentation_stat)
        for scope in [scope for scope in self.getScopes(excludeContains=True)[::-1]
                      if 'func:' not in scope.path and
                         (scope.path in stopScopes or
                          self.tree.isUnderStopScopes(scope.path, stopScopes,
                                                      includeInterfaces=True))]:
            indexRemoved = []

            # 1 - Remove all DO loops on JI and JJ for preparation to compute on KLEV only
            # Look for all do-nodes, check if the loop-index is one of the authorized
            # list (indexToCheck), if found, removes it
            for doNode in scope.findall('.//{*}do-construct')[::-1]:
                for loopI in doNode.findall('./{*}do-stmt/{*}do-V/{*}named-E/{*}N'):
                    loopIname = n2name(loopI).upper()
                    if loopIname in indexToCheck:
                        # Move the content of the doNode (except do-stmt and end_do_stmt)
                        # in parent node
                        par = self.getParent(doNode)
                        index = list(par).index(doNode)
                        for item in doNode[1:-1][::-1]:
                            par.insert(index, item)
                        par.remove(doNode)  # remove the do_construct
                        if loopIname not in indexRemoved:
                            indexRemoved.append(loopIname)

            # 2 - Reduce horizontal dimensions for intrinsic array functions
            # SUM(X(:,:)) => SUM(X(JI, X))
            # In the simplify==True case, SUM(X(:,:)) becomes SUM(X(:)) by removing first dimension
            for intr in scope.findall('.//{*}R-LT/{*}parens-R/../..'):
                intrName = n2name(intr.find('./{*}N')).upper()
                if intrName in ('PACK', 'UNPACK', 'COUNT', 'MAXVAL', 'MINVAL', 'ALL', 'ANY', 'SUM'):
                    # Is it part of an expression or of an affectation statement?
                    # eg: CALL(UNPACK(Y(:), MASK=G(:,:)) * Z(:))
                    # or  X(:,:) = UNPACK(Y(:), MASK=G(:,:)) * Z(:)
                    # If yes, we also need to transform X and Z
                    # if not, only arrays inside the function are transformed
                    parToUse = intr
                    par = intr
                    while par is not None and not isStmt(par):
                        par = self.getParent(par)
                        if tag(par) in ('a-stmt', 'op-E'):
                            parToUse = par

                    # 2.1 Loop on all arrays in the expression using this intrinsic function
                    #     to replace horizontal dimensions by indexes
                    for namedE in parToUse.findall('.//{*}R-LT/{*}array-R/../..'):
                        slice2index(namedE, scope.path)

                    # 2.2 Replace intrinsic function when argument becomes a scalar
                    if intr.find('.//{*}R-LT/{*}array-R') is None:
                        if intrName in ('MAXVAL', 'MINVAL', 'SUM', 'ALL', 'ANY'):
                            # eg: MAXVAL(X(:)) => MAXVAL(X(JI)) => X(JI)
                            parens = intr.find('./{*}R-LT/{*}parens-R')
                            parens.tag = f'{{{NAMESPACE}}}parens-E'
                            intrPar = self.getParent(intr)
                            intrPar.insert(list(intrPar).index(intr), parens)
                            intrPar.remove(intr)
                        elif intrName == 'COUNT':
                            # eg: COUNT(X(:)) => COUNT(X(JI)) => MERGE(1, 0., X(JI))
                            nodeN = intr.find('./{*}N')
                            for item in nodeN[1:]:
                                nodeN.remove(item)
                            nodeN.find('./{*}n').text = 'MERGE'
                            elementLT = intr.find('./{*}R-LT/{*}parens-R/{*}element-LT')
                            for val in (1, 0):
                                element = createElem('element', tail=', ')
                                element.append(createExprPart(val))
                                elementLT.insert(0, element)

            if simplify:
                # 3 - Remove useless dimensions
                # Arrays only on horizontal dimensions are transformed into scalars
                #  - at declaration "REAL :: P(D%NIT)" => "REAL :: P"
                #  - during call "CALL FOO(P(:)" => "CALL FOO(P)"
                #                "CALL FOO(Z(:,IK)" => "CALL FOO(Z(JIJ,IK)"
                #  - but "CALL FOO(Z(:,:)" is kept untouched
                # All arrays are transformed except IN/OUT arrays of the top subroutine (stopScopes)
                # that cannot be transformed into scalar

                # At least for rain_ice.F90, inlining must be performed before executing this code
                assert self.find('.//{*}include') is None and \
                       self.find('.//{*}include-stmt') is None, \
                       "inlining must be performed before removing horizontal dimensions"

                if scope.path in stopScopes:
                    # List of dummy arguments whose shape cannot be modified
                    preserveShape = [v['n'] for v in scope.varList if v['arg']]
                else:
                    preserveShape = []

                # 4 - For all subroutines or modi_ interface
                if 'sub:' in scope.path:
                    # Remove suppressed dimensions "Z(JIJI)" => "Z"
                    # We cannot do this based upon declaration transformation because an array can
                    # be declared in one scope and used in another sub-scope
                    for namedE in scope.findall('.//{*}named-E/{*}R-LT/{*}parens-R/../..'):
                        if n2name(namedE.find('./{*}N')).upper() not in preserveShape:
                            var = scope.varList.findVar(n2name(namedE.find('./{*}N')).upper())
                            if var is not None and var['as'] is not None and len(var['as']) > 0:
                                subs = namedE.findall('./{*}R-LT/{*}parens-R/' +
                                                      '{*}element-LT/{*}element')
                                if (len(subs) == 1 and var['as'][0][1] in hUupperBounds) or \
                                   (len(subs) == 2 and (var['as'][0][1] in hUupperBounds and
                                                        var['as'][1][1] in hUupperBounds)):
                                    namedE.remove(namedE.find('./{*}R-LT'))

                    # Remove (:) or (:,:) for horizontal array in call-statement
                    # or replace ':' by index
                    for call in scope.findall('.//{*}call-stmt'):
                        for namedE in call.findall('./{*}arg-spec//{*}named-E'):
                            subs = namedE.findall('.//{*}section-subscript')
                            var = scope.varList.findVar(n2name(namedE.find('./{*}N')).upper())
                            if len(subs) > 0 and (var is None or var['as'] is None or
                                                  len(var['as']) < len(subs)):
                                # Before adding a warning, functions (especially unpack) must
                                # be recognised
                                # logging.warning(("Don't know if first dimension of {name} must " +
                                #                 "be modified or not -> kept untouched"
                                #                ).format(name=alltext(namedE)))
                                remove = False  # to remove completly the parentheses
                                index = False  # to transform ':' into index
                            elif (len(subs) >= 2 and
                                  ':' in alltext(subs[0]) and var['as'][0][1] in hUupperBounds and
                                  ':' in alltext(subs[1]) and var['as'][1][1] in hUupperBounds):
                                # eg: CALL(P(:, :)) with SIZE(P, 1) == D%NIT and SIZE(P, 2) == D%NJT
                                remove = len(subs) == 2
                                index = (len(subs) > 2 and
                                         len([sub for sub in subs if ':' in alltext(sub)]) == 2)
                            elif (len(subs) >= 1 and
                                  ':' in alltext(subs[0]) and var['as'][0][1] in hUupperBounds):
                                # eg: CALL(P(:)) with SIZE(P, 1) == D%NJT
                                remove = len(subs) == 1
                                index = (len(subs) > 1 and
                                         len([sub for sub in subs if ':' in alltext(sub)]) == 1)
                            else:
                                remove = False
                                index = False
                            if remove:
                                if n2name(namedE.find('./{*}N')).upper() in preserveShape:
                                    slice2index(namedE, scope.path)
                                else:
                                    nodeRLT = namedE.find('.//{*}R-LT')
                                    self.getParent(nodeRLT).remove(nodeRLT)
                            if index:
                                slice2index(namedE, scope.path)

                    # Remove dimensions in variable declaration statements
                    # This modification must be done after other modifications so that
                    # the findVar method still return an array
                    for decl in scope.findall('.//{*}T-decl-stmt/{*}EN-decl-LT/{*}EN-decl'):
                        name = n2name(decl.find('./{*}EN-N/{*}N')).upper()
                        if name not in preserveShape:
                            varsShape = decl.findall('.//{*}shape-spec-LT')
                            for varShape in varsShape:
                                subs = varShape.findall('.//{*}shape-spec')
                                if (len(subs) == 1 and alltext(subs[0]) in hUupperBounds) or \
                                   (len(subs) == 2 and (alltext(subs[0]) in hUupperBounds and
                                                        alltext(subs[1]) in hUupperBounds)):
                                    # Transform array declaration into scalar declaration
                                    itemToRemove = self.getParent(varShape)
                                    self.getParent(itemToRemove).remove(itemToRemove)
                                    # We should set self.varList to None here to clear the cache
                                    # but we don't to save some computational time

            # 4 - Values for removed indexes
            for loopIndex in indexRemoved:
                # Initialize former indexes JI,JJ,JIJ to first array element:
                # JI=D%NIB, JJ=D%NJB, JIJ=D%NIJB
                self.insertStatement(
                    scope.path,
                    createExpr(loopIndex + " = " + indexToCheck[loopIndex][0])[0], True)
                self.addArgInTree(scope.path, 'D', 'TYPE(DIMPHYEX_t) :: D',
                                  0, stopScopes, moduleVarList=[('MODD_DIMPHYEX', ['DIMPHYEX_t'])],
                                  parser=parser, parserOptions=parserOptions, wrapH=wrapH)
            # Check loop index presence at declaration of the scope
            self.addVar([[scope.path, loopIndex, 'INTEGER :: ' + loopIndex, None]
                         for loopIndex in indexRemoved
                         if scope.varList.findVar(loopIndex, exactScope=True) is None])

    @debugDecor
    def removePHYEXUnusedLocalVar(self, scopePath=None, excludeList=None, simplify=False):
        """
        Remove unused local variables (dummy and module variables are not suppressed)
        This function is identical to variables.removeUnusedLocalVar except that this one
        is specific to the PHYEX code and take into account the mnh_expand directives.
        :param scopePath: scope paths to explore (None for all)
        :param excludeList: list of variable names to exclude from removal (even if unused)
        :param simplify: try to simplify code (if we delete a declaration statement that used a
                         variable as kind selector, and if this variable is not used else where,
                         we also delete it)
        """

        # Look for variables needed for the mnh_expand directives
        for node in self.findall('.//{*}C'):
            if node.text.startswith('!$mnh_expand_array(') or \
               node.text.startswith('!$mnh_expand_where('):
                if excludeList is None:
                    excludeList = []
                elems = node.text.split('(')[1].split(')')[0].split(',')
                excludeList.extend([v.strip().upper() for v in [e.split('=')[0] for e in elems]])
        return self.removeUnusedLocalVar(scopePath=scopePath, excludeList=excludeList,
                                         simplify=simplify)

    @debugDecor
    def expandAllArraysPHYEX(self, concurrent=False):
        """
        Transform array syntax into DO loops
        :param concurrent: use 'DO CONCURRENT' instead of simple 'DO' loops
        """

        # For simplicity, all functions (not only array functions) have been searched
        # in the PHYEX source code
        funcList = ['AA2', 'AA2W', 'AF3', 'AM3', 'ARTH', 'BB3', 'BB3W', 'COEFJ', 'COLL_EFFI',
                    'DELTA', 'DELTA_VEC', 'DESDTI', 'DESDTW', 'DQSATI_O_DT_1D',
                    'DQSATI_O_DT_2D_MASK', 'DQSATI_O_DT_3D', 'DQSATW_O_DT_1D',
                    'DQSATW_O_DT_2D_MASK', 'DQSATW_O_DT_3D', 'DSDD', 'DXF', 'DXM', 'DYF',
                    'DYM', 'DZF', 'DZM', 'ESATI', 'ESATW', 'FUNCSMAX', 'GAMMA_INC', 'GAMMA_X0D',
                    'GAMMA_X1D', 'GENERAL_GAMMA', 'GET_XKER_GWETH', 'GET_XKER_N_GWETH',
                    'GET_XKER_N_RACCS', 'GET_XKER_N_RACCSS', 'GET_XKER_N_RDRYG',
                    'GET_XKER_N_SACCRG', 'GET_XKER_N_SDRYG', 'GET_XKER_N_SWETH', 'GET_XKER_RACCS',
                    'GET_XKER_RACCSS', 'GET_XKER_RDRYG', 'GET_XKER_SACCRG', 'GET_XKER_SDRYG',
                    'GET_XKER_SWETH', 'GX_M_M', 'GX_M_U', 'GX_U_M', 'GX_V_UV', 'GX_W_UW', 'GY_M_M',
                    'GY_M_V', 'GY_U_UV', 'GY_V_M', 'GY_W_VW', 'GZ_M_M', 'GZ_M_W', 'GZ_U_UW',
                    'GZ_V_VW', 'GZ_W_M', 'HYPGEO', 'ICENUMBER2', 'LEAST_LL', 'LNORTH_LL',
                    'LSOUTH_LL', 'LWEST_LL', 'MOMG', 'MXF', 'MXM', 'MYF', 'MYM', 'MZF', 'MZM',
                    'QSATI_0D', 'QSATI_1D', 'QSATI_2D', 'QSATI_2D_MASK', 'QSATI_3D',
                    'QSATMX_TAB', 'QSATW_0D', 'QSATW_1D', 'QSATW_2D', 'QSATW_2D_MASK',
                    'QSATW_3D', 'RECT', 'REDIN', 'SINGL_FUNCSMAX', 'SM_FOES_0D', 'SM_FOES_1D',
                    'SM_FOES_2D', 'SM_FOES_2D_MASK', 'SM_FOES_3D', 'SM_PMR_HU_1D', 'SM_PMR_HU_3D',
                    'TIWMX_TAB', 'TO_UPPER', 'ZRIDDR', 'GAMMLN', 'COUNTJV2D', 'COUNTJV3D', 'UPCASE']

        return self.removeArraySyntax(concurrent=concurrent, useMnhExpand=False,
                                      loopVar=_loopVarPHYEX, reuseLoop=False,
                                      funcList=funcList, updateMemSet=True, updateCopy=True)

    @debugDecor
    def mathFunctoBRFunc(self):
        """
        Convert intrinsic math functions **, LOG, ATAN, **2, **3, **4, EXP, COS, SIN, ATAN2
        into a self defined function BR_ for MesoNH CPU/GPU bit-reproductibility
        """
        # Power integer allowed for BR_Pn and functions converted (from modi_bitrep.f90)
        powerBRList = [2, 3, 4]
        mathBRList = ['ALOG', 'LOG', 'EXP', 'COS', 'SIN', 'ASIN', 'ATAN', 'ATAN2']

        for scope in self.getScopes():
            # 1/2 Look for all operations and seek for power **
            # <f:op-E>
            #  ... ==> leftOfPow
            #  <f:op>
            #    <f:o>**</f:o>
            #  </f:op>
            #  ... ==> rightOfPow
            # </f:op-E>
            for opo in scope.findall('.//{*}o'):
                if alltext(opo) == '**':
                    op = scope.getParent(opo)
                    opE = scope.getParent(opo, level=2)
                    parOfopE = scope.getParent(opo, level=3)
                    # Save the position of the opE that will be converted
                    index = list(parOfopE).index(opE)

                    # Get the next/previous object after/before the ** operator which are
                    # the siblings of the parent of <f:o>*</f:o>
                    rightOfPow = scope.getSiblings(op, after=True, before=False)[0]
                    leftOfPow = scope.getSiblings(op, after=False, before=True)[0]

                    # Prepare the object that will contain the left and right of Pow
                    nodeRLT = createElem('R-LT')
                    parensR = createElem('parens-R', text='(', tail=')')
                    elementLT = createElem('element-LT')

                    # Regarding the right part of pow(), build a new node expression :
                    #  If it is a number and check only for 2, 3 and 4 (e.g. A**2, B**3, D**4 etc)
                    if tag(rightOfPow) == 'literal-E':
                        # Handle '2.' and '2.0'
                        powerNumber = int(alltext(rightOfPow).replace('.', ''))
                        if powerNumber in powerBRList:
                            # <f:named-E>
                            #   <f:N>
                            #     <f:n>BR_Pn</f:n>
                            #   </f:N>
                            #   <f:R-LT>
                            #     <f:parens-R>(
                            #       <f:element-LT>
                            #       <f:element>
                            #   	... ==> leftOfPow
                            #       </f:element>,
                            #       </f:element-LT>
                            #     </f:parens-R>)
                            #   </f:R-LT>
                            # </f:named-E>
                            nodeBRP = createExprPart('BR_P' + str(powerNumber))
                            element = createElem('element')
                            element.append(leftOfPow)
                            elementLT.append(element)
                    # If the right part of pow() is not a number OR it is a number
                    # except 2, 3 or 4 (powerBRList)
                    if tag(rightOfPow) != 'literal-E' or \
                       (tag(rightOfPow) == 'literal-E' and
                           int(alltext(rightOfPow).replace('.', '')) not in powerBRList):
                        # <f:named-E>
                        #   <f:N>
                        #     <f:n>BR_POW</f:n>   or <f:n>BR_Pn</f:n>
                        #   </f:N>
                        #   <f:R-LT>
                        #     <f:parens-R>(
                        #       <f:element-LT>
                        #       <f:element>
                        #   	... ==> leftOfPow
                        #       </f:element>,
                        #         <f:element>
                        #   	  ... ==> rightOfPow
                        #        </f:element>
                        #       </f:element-LT>
                        #     </f:parens-R>)
                        #   </f:R-LT>
                        # </f:named-E>
                        nodeBRP = createExprPart('BR_POW')
                        leftElement = createElem('element', tail=',')
                        leftElement.append(leftOfPow)
                        rightElement = createElem('element')
                        rightElement.append(rightOfPow)
                        elementLT.append(leftElement)
                        elementLT.append(rightElement)

                    # Insert the RLT object as a sibling of the BR_ object,
                    # e.g. instead of the old object
                    parensR.append(elementLT)
                    nodeRLT.append(parensR)
                    nodeBRP.insert(1, nodeRLT)
                    nodeBRP.tail = opE.tail
                    parOfopE.remove(opE)
                    parOfopE.insert(index, nodeBRP)

                    # Add necessary module in the current scope
                    self.addModuleVar([(scope.path, 'MODI_BITREP', None)])

            # 2/2 Look for all specific functions LOG, ATAN, EXP,etc
            for nnn in scope.findall('.//{*}named-E/{*}N/{*}n'):
                if alltext(nnn).upper() in mathBRList:
                    if alltext(nnn).upper() == 'ALOG':
                        nnn.text = 'BR_LOG'
                    else:
                        nnn.text = 'BR_' + nnn.text
                    # Add necessary module in the current scope
                    self.addModuleVar([(scope.path, 'MODI_BITREP', None)])

    @debugDecor
    def shumanFUNCtoCALL(self):
        """
        Convert all calling of functions and gradient present in shumansGradients
        table into the use of subroutines
        and use mnh_expand_directives to handle intermediate computations
        """
        def getDimsAndMNHExpandIndexes(zshugradwkDim, dimWorkingVar=''):
            dimSuffRoutine = ''
            if zshugradwkDim == 1:
                dimSuffRoutine = '2D'  # e.g. in turb_ver_dyn_flux : MZM(ZCOEFS(:,IKB))
                dimSuffVar = '1D'
                mnhExpandArrayIndexes = 'JIJ=IIJB:IIJE'
            elif zshugradwkDim == 2:
                dimSuffVar = '2D'
                if 'D%NKT' in dimWorkingVar:
                    mnhExpandArrayIndexes = 'JIJ=IIJB:IIJE,JK=1:IKT'
                elif 'D%NIT' in dimWorkingVar and 'D%NJT' in dimWorkingVar:
                    # only found in turb_hor*
                    mnhExpandArrayIndexes = 'JI=1:IIT,JJ=1:IJT'
                    dimSuffRoutine = '2D'  # e.g. in turb_hor : MZM(PRHODJ(:,:,IKB))
                else:
                    # raise PYFTError('mnhExpandArrayIndexes construction case ' +
                    #                 'is not handled, case for zshugradwkDim == 2, ' +
                    #                 "dimWorkingVar = ' + dimWorkingVar)
                    dimSuffRoutine = ''
                    mnhExpandArrayIndexes = 'JIJ=IIJB:IIJE,JK=1:IKT'
            elif zshugradwkDim == 3:  # case for turb_hor 3D variables
                dimSuffVar = '3D'
                mnhExpandArrayIndexes = 'JI=1:IIT,JJ=1:IJT,JK=1:IKT'
            else:
                raise PYFTError('Shuman func to routine conversion not implemented ' +
                                'for 4D+ dimensions variables')
            return dimSuffRoutine, dimSuffVar, mnhExpandArrayIndexes

        def FUNCtoROUTINE(scope, stmt, itemFuncN, localShumansCount, inComputeStmt,
                          nbzshugradwk, zshugradwkDim, dimWorkingVar):
            """
            :param scope: node on which the calling function is present before transformation
            :param stmt: statement node (a-stmt or call-stmt) that contains the function(s) to be
                         transformed
            :param itemFuncN: <n>FUNCTIONNAME</n> node
            :param localShumansCount: instance of the shumansGradients dictionnary
                                      for the given scope (which contains the number of times a
                                      function has been called within a transformation)
            :param dimWorkingVar: string of the declaration of a potential working variable
                                  depending on the array on wich the shuman is applied
                                  (e.g. MZM(PRHODJ(:,IKB));
                                        dimWorkingVar = 'REAL, DIMENSION(D%NIJT) :: ' )
            :return zshugradwk
            :return callStmt: the new CALL to the routines statement
            :return computeStmt: the a-stmt computation statement if there was an operation
                                 in the calling function in stmt
            """
            # Function name, parent and grandParent
            parStmt = self.getParent(stmt)
            parItemFuncN = scope.getParent(itemFuncN)  # <N><n>MZM</N></n>
            # <named-E><N><n>MZM</N></n> <R-LT><f:parens-R>(<f:element-LT><f:element>....
            grandparItemFuncN = scope.getParent(itemFuncN, level=2)
            funcName = alltext(itemFuncN)

            # workingItem = Content of the function
            indexForCall = list(parStmt).index(stmt)
            if inComputeStmt:
                # one for !$mnh_expand, one for !$acc kernels added at the previous
                # call to FUNCtoROUTINE
                indexForCall -= 2
            siblsItemFuncN = scope.getSiblings(parItemFuncN, after=True, before=False)
            workingItem = siblsItemFuncN[0][0][0]
            # Case where & is present in the working item.
            # We must look for all contents until the last ')'
            if len(siblsItemFuncN[0][0]) > 1:
                # last [0] is to avoid getting the '( )' from the function
                workingItem = self.updateContinuation(siblsItemFuncN[0][0], removeALL=True,
                                                      align=False, addBegin=False)[0]

            # Detect if the workingItem contains expressions, if so:
            # create a compute statement embedded by mnh_expand directives
            opE = workingItem.findall('.//{*}op-E')
            self.addArrayParenthesesInNode(workingItem, scope.path)
            computeStmt = []
            dimSuffVar = str(zshugradwkDim) + 'D'
            dimSuffRoutine, dimSuffVar, mnhExpandArrayIndexes = \
                getDimsAndMNHExpandIndexes(zshugradwkDim, dimWorkingVar)
            if len(opE) > 0:
                nbzshugradwk += 1
                computingVarName = 'ZSHUGRADWK'+str(nbzshugradwk)+'_'+str(zshugradwkDim)+'D'
                # Add the declaration of the new computing var and workingVar if not already present
                if not scope.varList.findVar(computingVarName):
                    self.addVar([[scope.path, computingVarName,
                                  dimWorkingVar + computingVarName, None]])
                else:
                    # Case of nested shuman/gradients with a working variable already declared.
                    # dimWorkingVar is only set again for mnhExpandArrayIndexes
                    computeVar = scope.varList.findVar(computingVarName)
                    dimWorkingVar = 'REAL, DIMENSION('
                    for dims in computeVar['as'][:arrayDim]:
                        dimWorkingVar += dims[1] + ','
                    dimWorkingVar = dimWorkingVar[:-1] + ') ::'

                dimSuffRoutine, dimSuffVar, mnhExpandArrayIndexes = \
                    getDimsAndMNHExpandIndexes(zshugradwkDim, dimWorkingVar)

                # Insert the directives and the compute statement
                mnhOpenDir = "!$mnh_expand_array(" + mnhExpandArrayIndexes + ")"
                mnhCloseDir = "!$mnh_end_expand_array(" + mnhExpandArrayIndexes + ")"
                computeStmt = createExpr(computingVarName + " = " + alltext(workingItem))[0]
                workingItem = computeStmt.find('.//{*}E-1')
                parStmt.insert(indexForCall, createElem('C', text='!$acc kernels', tail='\n'))
                parStmt.insert(indexForCall + 1, createElem('C', text=mnhOpenDir, tail='\n'))
                parStmt.insert(indexForCall + 2, computeStmt)
                parStmt.insert(indexForCall + 3, createElem('C', text=mnhCloseDir, tail='\n'))
                parStmt.insert(indexForCall + 4, createElem('C',
                               text='!$acc end kernels', tail='\n'))
                parStmt.insert(indexForCall + 5, createElem('C',
                               text='!', tail='\n'))  # To increase readibility
                indexForCall += 6

            # Add the new CALL statement
            if zshugradwkDim == 1:
                dimSuffRoutine = '2D'
            workingVar = 'Z' + funcName + dimSuffVar + '_WORK' + str(localShumansCount[funcName])
            gpuGradientImplementation = '_PHY(D, '
            if funcName in ('GY_U_UV', 'GX_V_UV'):
                gpuGradientImplementation = '_DEVICE('
            callStmt = createExpr("CALL " + funcName + dimSuffRoutine +
                                  gpuGradientImplementation + alltext(workingItem) +
                                  ", " + workingVar + ")")[0]
            parStmt.insert(indexForCall, callStmt)

            # Remove the function/gradient from the original statement
            parOfgrandparItemFuncN = scope.getParent(grandparItemFuncN)
            indexWorkingVar = list(parOfgrandparItemFuncN).index(grandparItemFuncN)
            savedTail = grandparItemFuncN.tail
            parOfgrandparItemFuncN.remove(grandparItemFuncN)

            # Add the working variable within the original statement
            xmlWorkingvar = createExprPart(workingVar)
            xmlWorkingvar.tail = savedTail
            parOfgrandparItemFuncN.insert(indexWorkingVar, xmlWorkingvar)

            # Add the declaration of the shuman-gradient workingVar if not already present
            if not scope.varList.findVar(workingVar):
                self.addVar([[scope.path, workingVar, dimWorkingVar + workingVar, None]])

            return callStmt, computeStmt, nbzshugradwk

        shumansGradients = {'MZM': 0, 'MXM': 0, 'MYM': 0, 'MZF': 0, 'MXF': 0, 'MYF': 0,
                            'DZM': 0, 'DXM': 0, 'DYM': 0, 'DZF': 0, 'DXF': 0, 'DYF': 0,
                            'GZ_M_W': 0, 'GZ_W_M': 0, 'GZ_U_UW': 0, 'GZ_V_VW': 0,
                            'GX_M_U': 0, 'GX_U_M': 0, 'GX_W_UW': 0, 'GX_M_M': 0,
                            'GY_V_M': 0, 'GY_M_V': 0, 'GY_W_VW': 0, 'GY_M_M': 0,
                            'GX_V_UV': 0, 'GY_U_UV': 0}
        scopes = self.getScopes()
        if scopes[0].path.split('/')[-1].split(':')[1][:4] == 'MODD':
            return
        for scope in scopes:
            if 'sub:' in scope.path and 'func' not in scope.path \
               and 'interface' not in scope.path:
                # Init : look for all a-stmt and call-stmt which contains a shuman or
                # gradients function, and save it into a list foundStmtandCalls
                foundStmtandCalls, computeStmtforParenthesis = {}, []
                aStmt = self.findall('.//{*}a-stmt')
                callStmts = self.findall('.//{*}call-stmt')
                aStmtandCallStmts = aStmt + callStmts
                for stmt in aStmtandCallStmts:
                    elemN = stmt.findall('.//{*}n')
                    for el in elemN:
                        if alltext(el) in list(shumansGradients):
                            # Expand the single-line if-stmt necessary
                            # to add all the new lines further.
                            parStmt = scope.getParent(stmt)
                            if tag(parStmt) == 'action-stmt':
                                self.changeIfStatementsInIfConstructs(
                                    singleItem=scope.getParent(parStmt))

                            if str(stmt) in foundStmtandCalls:
                                foundStmtandCalls[str(stmt)][1] += 1
                            else:
                                foundStmtandCalls[str(stmt)] = [stmt, 1]

                # For each a-stmt and call-stmt containing at least 1 shuman/gradient function
                for stmt in foundStmtandCalls:
                    localShumansGradients = copy.deepcopy(shumansGradients)
                    elemToLookFor = [foundStmtandCalls[stmt][0]]
                    previousComputeStmt = []
                    maxnbZshugradwk = 0

                    while len(elemToLookFor) > 0:
                        nbzshugradwk = 0
                        for elem in elemToLookFor:
                            elemN = elem.findall('.//{*}n')
                            for el in elemN:
                                if alltext(el) in list(localShumansGradients.keys()):
                                    # Check the dimensions of the stmt objects in which the
                                    # function exist for handling selecting-index
                                    # shuman-function use
                                    # 1) if the stmt is from an a-astmt, check E1
                                    nodeE1var = foundStmtandCalls[stmt][0].findall(
                                                './/{*}E-1/{*}named-E/{*}N')
                                    if len(nodeE1var) > 0:
                                        var = scope.varList.findVar(alltext(nodeE1var[0]))
                                        allSubscripts = foundStmtandCalls[stmt][0].findall(
                                                            './/{*}E-1//{*}named-E/{*}R-LT/' +
                                                            '{*}array-R/{*}section-subscript-LT')
                                    # 2) if the stmt is from a call-stmt,
                                    #    check the first <named-E><N> in the function
                                    else:
                                        elPar = self.getParent(el, level=2)  # MXM(...)
                                        callVar = elPar.findall('.//{*}named-E/{*}N')
                                        if alltext(el)[0] == 'G':
                                            # If it is a gradient, the array on which the gradient
                                            # is applied is the last argument

                                            # callVar[-1] is array on which the gradient is applied
                                            var = scope.varList.findVar(alltext(callVar[-1]))
                                            shumanIsCalledOn = scope.getParent(callVar[-1])
                                        else:
                                            # Shumans
                                            var, inested = None, 0
                                            # pylint: disable-next=unsubscriptable-object
                                            while not var or len(var['as']) == 0:
                                                # While the var is not an array already declared
                                                # callVar[0] is the first array on which the
                                                # function is applied
                                                var = scope.varList.findVar(
                                                           alltext(callVar[inested]))
                                                inested += 1
                                            shumanIsCalledOn = scope.getParent(callVar[inested-1])
                                        allSubscripts = shumanIsCalledOn.findall(
                                                            './/{*}R-LT/{*}array-R/' +
                                                            '{*}section-subscript-LT')

                                    # if var: # Protection in case of nested functions,
                                    # var is not an array but None
                                    arrayDim = len(var['as'])

                                    # Look for subscripts in case of array sub-selection
                                    # (such as 1 or IKB)
                                    if len(allSubscripts) > 0:
                                        for subLT in allSubscripts:
                                            for sub in subLT:
                                                lowerBound = sub.findall('.//{*}lower-bound')
                                                if len(lowerBound) > 0:
                                                    if len(sub.findall('.//{*}upper-bound')) > 0:
                                                        # For protection: not handled with
                                                        #                 lower:upper bounds
                                                        raise PYFTError('ShumanFUNCtoCALL does ' +
                                                                        'not handle conversion ' +
                                                                        'to routine of array ' +
                                                                        'subselection lower:upper' +
                                                                        ': how to set up the ' +
                                                                        'shape of intermediate ' +
                                                                        'arrays ?')
                                                    # Handle change of dimensions for
                                                    # selecting index for the working arrays
                                                    arrayDim -= 1

                                    # Build the dimensions declaration in case of
                                    # working/intermediate variable needed
                                    dimWorkingVar = ''
                                    if var:
                                        dimWorkingVar = 'REAL, DIMENSION('
                                        for dims in var['as'][:arrayDim]:
                                            dimWorkingVar += dims[1] + ','
                                        dimWorkingVar = dimWorkingVar[:-1] + ') ::'

                                    # Add existing working variable with the name of the function
                                    localShumansGradients[alltext(el)] += 1

                                    # To be sure that ending !comments after the statement is
                                    # not impacting the placement of the last !mnh_expand_array
                                    if foundStmtandCalls[stmt][0].tail:
                                        foundStmtandCalls[stmt][0].tail = \
                                            foundStmtandCalls[stmt][0].tail.replace('\n', '') + '\n'
                                    else:
                                        foundStmtandCalls[stmt][0].tail = '\n'

                                    # Transform the function into a call statement
                                    (newCallStmt, newComputeStmt,
                                     nbzshugradwk) = FUNCtoROUTINE(scope, elem, el,
                                                                   localShumansGradients,
                                                                   elem in previousComputeStmt,
                                                                   nbzshugradwk, arrayDim,
                                                                   dimWorkingVar)
                                    # Update the list of elements to check if there are still
                                    # remaining function to convert within the new call-stmt
                                    elemToLookFor.append(newCallStmt)

                                    # If a new intermediate compute statement was created, it needs
                                    # to be checked and add Parenthesis to arrays for mnh_expand
                                    if len(newComputeStmt) > 0:
                                        elemToLookFor.append(newComputeStmt)
                                        computeStmtforParenthesis.append(newComputeStmt)
                                        # Allow to save that this newComputeStmt comes with 2
                                        # extra lines before and after
                                        # (mnh_expand and acc directives)
                                        previousComputeStmt.append(newComputeStmt)
                                    break
                        # Check in old and new objects if there are still
                        # remaining shuman/gradients functions
                        elemToLookForNew = []
                        for i in elemToLookFor:
                            nodeNs = i.findall('.//{*}n')
                            if len(nodeNs) > 0:
                                for nnn in nodeNs:
                                    if alltext(nnn) in list(localShumansGradients):
                                        elemToLookForNew.append(i)
                                        break
                        elemToLookFor = elemToLookForNew
                        # Save the maximum number of necessary intermediate
                        # computing variables ZSHUGRADWK
                        if nbzshugradwk > maxnbZshugradwk:
                            maxnbZshugradwk = nbzshugradwk

                    # Add parenthesis around all variables
                    self.addArrayParenthesesInNode(foundStmtandCalls[stmt][0], scope.path)

                    # For the last compute statement, add mnh_expand and acc
                    # kernels if not call statement
                    if tag(foundStmtandCalls[stmt][0]) != 'call-stmt':
                        # get mnhExpandArrayIndexes
                        # Here dimSuffRoutine, dimSuffVar are not used
                        dimSuffRoutine, dimSuffVar, mnhExpandArrayIndexes = \
                            getDimsAndMNHExpandIndexes(arrayDim, dimWorkingVar)

                        parStmt = self.getParent(foundStmtandCalls[stmt][0])
                        indexForCall = list(parStmt).index(foundStmtandCalls[stmt][0])
                        mnhOpenDir = "!$mnh_expand_array(" + mnhExpandArrayIndexes + ")"
                        mnhCloseDir = "!$mnh_end_expand_array(" + mnhExpandArrayIndexes + ")"
                        parStmt.insert(indexForCall,
                                       createElem('C', text="!$acc kernels", tail='\n'))
                        parStmt.insert(indexForCall + 1,
                                       createElem('C', text=mnhOpenDir, tail='\n'))
                        parStmt.insert(indexForCall + 3,
                                       createElem('C', text=mnhCloseDir, tail='\n'))
                        parStmt.insert(indexForCall + 4,
                                       createElem('C', text="!$acc end kernels", tail='\n'))
                        parStmt.insert(indexForCall + 5,
                                       createElem('C', text="!", tail='\n'))  # For readibility

                # For all saved intermediate newComputeStmt, add parenthesis around all variables
                for stmt in computeStmtforParenthesis:
                    self.addArrayParenthesesInNode(stmt, scope.path)

    @debugDecor
    @noParallel
    @updateTree('signal')
    def buildACCTypeHelpers(self):
        """
        build module files containing helpers to copy user type structures
        """
        for scope in self.getScopes():
            if scope.path.split('/')[-1].split(':')[0] == 'type':
                typeName = scope.path.split('/')[-1].split(':')[1]
                filename = os.path.join(os.path.dirname(self.getFileName()),
                                        "modd_util_{t}.F90".format(t=typeName.lower()))
                self.tree.signal(filename)
                with open(filename, 'w', encoding="utf-8") as file:
                    file.write("""
MODULE MODD_UTIL_{t}
USE {m}, ONLY: {t}
CONTAINS
SUBROUTINE COPY_{t} (YD, LDCREATED)""".format(t=typeName,
                                              m=scope.path.split('/')[-2].split(':')[1]))

                    for var in scope.varList:
                        if 'TYPE(' in var['t'].replace(' ', '').upper():
                            file.write("""
USE MODD_UTIL_{t}""".format(t=var['t'].replace(' ', '')[5:-1]))

                    file.write("""
IMPLICIT NONE
TYPE ({t}), INTENT(IN), TARGET :: YD
LOGICAL, OPTIONAL, INTENT(IN) :: LDCREATED
INTEGER :: I
LOGICAL :: LLCREATED
LLCREATED = .FALSE.
IF (PRESENT (LDCREATED)) THEN
  LLCREATED = LDCREATED
ENDIF
IF (.NOT. LLCREATED) THEN
  !$acc enter data create (YD)
  !$acc update device (YD)
ENDIF""".format(t=typeName))

                    for var in scope.varList:
                        if var['allocatable']:
                            file.write("""
IF (ALLOCATED (YD%{v})) THEN
  !$acc enter data create (YD%{v})
  !$acc update device (YD%{v})
  !$acc enter data attach (YD%{v})
ENDIF""".format(v=var['n']))
                        if 'TYPE(' in var['t'].replace(' ', '').upper():
                            if var['as'] is not None and len(var['as']) != 0:
                                indexes = ['LBOUND(YD%{v}, 1) + I - 1'.format(v=var['n'])]
                                for i in range(len(var['as']) - 1):
                                    indexes.append('LBOUND(YD%{v}, {i})'.format(v=var['n'],
                                                   i=str(i + 2)))
                                file.write("""
DO I=1, SIZE(YD%{v})
  CALL COPY_{t}(YD%{v}({i}), LDCREATED=.TRUE.)
ENDDO""".format(v=var['n'], t=var['t'].replace(' ', '')[5:-1], i=', '.join(indexes)))
                            else:
                                file.write("""
CALL COPY_{t}(YD%{v}, LDCREATED=.TRUE.)""".format(v=var['n'], t=var['t'].replace(' ', '')[5:-1]))

                    file.write("""
END SUBROUTINE COPY_{t}

SUBROUTINE WIPE_{t} (YD, LDDELETED)""".format(t=typeName))

                    for var in scope.varList:
                        if 'TYPE(' in var['t'].replace(' ', '').upper():
                            file.write("""
USE MODD_UTIL_{t}""".format(t=var['t'].replace(' ', '')[5:-1]))

                    file.write("""
IMPLICIT NONE
TYPE ({t}), INTENT(IN), TARGET :: YD
LOGICAL, OPTIONAL, INTENT(IN) :: LDDELETED
INTEGER :: I
LOGICAL :: LLDELETED
LLDELETED = .FALSE.
IF (PRESENT (LDDELETED)) THEN
  LLDELETED = LDDELETED
ENDIF""".format(t=typeName))

                    for var in scope.varList:
                        if 'TYPE(' in var['t'].replace(' ', '').upper():
                            if var['as'] is not None and len(var['as']) != 0:
                                indexes = ['LBOUND(YD%{v}, 1) + I - 1'.format(v=var['n'])]
                                for i in range(len(var['as']) - 1):
                                    indexes.append('LBOUND(YD%{v}, {i})'.format(v=var['n'],
                                                   i=str(i + 2)))
                                file.write("""
DO I=1, SIZE(YD%{v})
  CALL WIPE_{t}(YD%{v}({i}), LDDELETED=.TRUE.)
ENDDO""".format(v=var['n'], t=var['t'].replace(' ', '')[5:-1], i=', '.join(indexes)))
                            else:
                                file.write("""
CALL WIPE_{t}(YD%{v}, LDDELETED=.TRUE.)""".format(v=var['n'], t=var['t'].replace(' ', '')[5:-1]))
                        if var['allocatable']:
                            file.write("""
IF (ALLOCATED (YD%{v})) THEN
  !$acc exit data detach (YD%{v})
  !$acc exit data delete (YD%{v})
ENDIF""".format(v=var['n']))

                    file.write("""
IF (.NOT. LLDELETED) THEN
  !$acc exit data delete (YD)
ENDIF
END SUBROUTINE WIPE_{t}

END MODULE MODD_UTIL_{t}\n""".format(t=typeName))
