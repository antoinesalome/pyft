"""
This module implements functions for high-to-moderate level transformation
"""

import xml.etree.ElementTree as ET
from util import (copy_doc,
                  alltext, needEtree, ETgetParent)

@needEtree
def deleteDrHook(doc, delParkind=True):
    """
    Find all pieces of fortran code where 'HOOK' is used, e.g. ZHOOK_HANDLE, CALL DR_HOOK, LHOOK, YOMHOOK
    and remove it.
    If delParkind=True:
    Find the module 'PARKIND1' with USE, ONLY: JPRB
    and remove it if JPRB is not used any more
    Exceptions for PARKIND1 deletion : USE PARKIND1 ; USE PARKIND1, ONLY: JPRB + other variables
    PARKIND1 deletion conditions :
        - ONLY must be present,
        - JPRB must not be present in other part of the tree.
    Limitations : fortran code must be in upper case; if JPRB is also used by other fortran code in the routine than Dr Hook
    TODO: improvement : if multiple 'USE PARKIND1, ONLY : JPRB' exist, there are not deleted because nb_JPRB > 1
    ==> must check if firstPar of item in testing the number of JPRB is not in a module USE
    :param doc: etree to use
    :param delParkind : boolean, True to delete PARKIND1 module use
    :return: modified doc
    """
    n=doc.findall('.//{*}n')
    itemtorm=[]
    paritemtorm=[]
    # Looking for HOOK
    for item in n:
        if 'HOOK' in alltext(item):
            par=ETgetParent(doc,item)
            firstPar=par
            # Look for the upper parent to be removed (but do not delete root objects)
            while 'file' not in str(par.tag) and 'object' not in str(par.tag) and 'program-unit' not in str(par.tag):
                firstPar=par
                par=ETgetParent(doc,par)
            # Check if the current firstParent was not already found by another item
            if firstPar not in itemtorm:
                paritemtorm.append(par)
                itemtorm.append(firstPar)
    # Suppression of HOOK : must be done before looking for JPRB in PARKIND1
    for i,elem in enumerate(itemtorm):
        paritemtorm[i].remove(elem)
    
    if delParkind:
        n=doc.findall('.//{*}n')
        itemtorm=[]
        paritemtorm=[]
        nb_JPRB = len([alltext(item) for item in n if 'JPRB' in alltext(item)])
        # Test first if JPRB is used only in the module USE (HOOK must be deleted first)
        if nb_JPRB==1:
            # Looking for JPRB from module PARKIND1
            for item in n:
                if 'JPRB' in alltext(item):
                    par=ETgetParent(doc,item)
                    firstPar=par
                    # Look for the upper parent to be removed (but do not delete root objects)
                    while 'file' not in str(par.tag) and 'object' not in str(par.tag) and 'program-unit' not in str(par.tag):
                        firstPar=par
                        par=ETgetParent(doc,par)
                    # Check conditions for deletion
                    if 'ONLY' in alltext(firstPar) and len(firstPar.findall('./{*}rename-LT/{*}rename')) == 1:
                        # Check if the current firstParent was not already found by another item
                        if firstPar not in itemtorm:
                            paritemtorm.append(par)
                            itemtorm.append(firstPar)  
            # Suppression
            for i,elem in enumerate(itemtorm):
                paritemtorm[i].remove(elem)

def changeIfStatementsInIfConstructs(doc):
    """
    Find all if-stmt and convert it to if-then-stmt
    E.g., before :
    IF(A=B) print*,"C
    after :
    IF(A=B) THEN
        print*,"C
    END IF
    :param doc: etree to use
    :return: modified doc
    """
    ifstmt = doc.findall('.//{*}if-stmt')
    for item in ifstmt:
        par=ETgetParent(doc,item)
        # Convert if-stmt to if-then-stmt and save current indentation from last sibling
        item.tag = 'if-then-stmt'
        curr_indent= par[par[:].index(item)-1].tail.replace('\n','')
        # Indentation is applied on current item.tail (for next Fortran line)
        item[0].tail += 'THEN\n'+curr_indent + '  '
        # Add end-if-stmt to the parent of the if-stmt
        endiftag=ET.Element('{http://fxtran.net/#syntax}end-if-stmt')
        endiftag.tail = '\n' + curr_indent+'END IF'
        item.append(endiftag)
        par[par[:].index(item)].extend(endiftag)
        # Remove cnt tag if any
        cnt = item.findall('./{*}cnt')
        if len(cnt) != 0:
            for i in cnt:
                item.remove(i)

class Applications():
    @copy_doc(deleteDrHook)
    def deleteDrHook(self):
        return deleteDrHook(doc=self._xml)
        
    @copy_doc(changeIfStatementsInIfConstructs)
    def changeIfStatementsInIfConstructs(self):
        return changeIfStatementsInIfConstructs(doc=self._xml)