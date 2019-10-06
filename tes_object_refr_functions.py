import idc
import idaapi
import traceback

from aenum import Enum

import DDE.IDAHelpers.addtrace
at = DDE.IDAHelpers.addtrace

ptrSize = 8
max_deepness = 10
max_hierarchy_deepness = 45
pdbg = False

class ConditionalFormat(object):
    def __init__(self, value):
        super(ConditionalFormat, self).__init__()

        if type(value) == NullObject:
            self.format = "{}"
            self.repr = repr(value)
        elif issubclass(type(value), MemObject):
            self.format = "0x{:X}"
            self.repr = value.addr
        else:
            self.format = "0x{:X}"
            self.repr = value

    def __repr__(self):
        return "<ConditionalFormat format: %s, repr: %s>" % (self.format, self.repr)

def addTraceTo(ea_or_mem_obj, bpt_size = 1):
    if issubclass(type(ea_or_mem_obj), BGSInventoryList):
        print("Adding trace to BGSInventoryList...")
        for item in ea_or_mem_obj.Items.Entries:
           at.addReadWriteTrace(item.addr, bpt_size)
        print("Done.")
    elif issubclass(type(ea_or_mem_obj), MemObject):
        print("Adding trace to MemObject...")
        at.addReadWriteTrace(ea_or_mem_obj.addr)
        print("Done.")
    else:
        print("Adding trace to address...")
        at.addReadWriteTrace(ea_or_mem_obj, bpt_size)
        print("Done.")

def hasChildrenOfType(classHierarchyDescriptor, typeName, deepness = 0):
    if deepness >= max_hierarchy_deepness: return False
    if classHierarchyDescriptor.hasChildren():
        children = classHierarchyDescriptor.getChildren(max_hierarchy_deepness)
        for child in children:
            # child = RTTIBaseClassDescriptor
            if child.typeDescriptor.name == typeName:
                return True
            else:
                if child.hasChildren():
                    return hasChildrenOfType(child.classHierarchyDescriptor, typeName, deepness + 1)
                return False
    return False

class MemObject(object):
    def __init__(self, addr, deepness = 0):
        self.addr = addr
        self.deepness = deepness

    def __repr__(self):
        return "<MemObject at 0x{:X}>".format(self.addr)

    def __eq__(self, other):
        if isinstance(other, MemObject):
            return self.addr == other.addr
        return False

    def __ne__(self, other):
        result = self.__eq__(other)

class NullObject(object):
    def __init__(self, *args, **kwargs):
        super(NullObject, self).__init__(*args, **kwargs)

    def __repr__(self):
        return "NULL"

def RVA(rva_addr):
    return idaapi.get_imagebase() + rva_addr

class BSExtraData(MemObject):
    def __init__(self, addr, deepness = 0):
        super(BSExtraData, self).__init__(addr, deepness)
        self.Next = idc.Qword(addr + BSExtraData.Offset.PtrNext.value)
        self.Type = idc.Byte(addr + BSExtraData.Offset.Type.value)

    def toArray(self):
        deepness = 0
        deepness_max = 10
        result = [self]
        current = self
        while deepness < deepness_max:
            if current.Next == 0:
                break
            deepness = deepness + 1
            nextExtra = BSExtraData(current.Next, self.deepness + 1)
            result.append(nextExtra)
            current = nextExtra
        return result

    def getTypeName(self):
        return VFTable(idc.Qword(self.addr + BSExtraData.Offset.vftable.value)).RTTICompleteObjectLocator.RTTITypeDescriptor.name

    def getExtraDataByType(self, extraDataType):
        flag = idc.Qword(self.addr + BSExtraData.Offset.field_10)
        if flag == 0:
            return None
        var1 = extraDataType >> 3
        if var1 < 0x1B:
            return None
        var2 = 1 << ((extraDataType & 0x7) & 0b11111111)

        if (idc.byte(var1 + flag) & var2) == 0:
            return None

        vftable = idc.Qword(self.addr + BSExtraData.Offset.vftable)
        if vftable == 0:
            return None

        current_extra_data_addr = self.addr
        while True:
            if idc.byte(current_extra_data_addr + BSExtraData.Offset.Type) == extraDataType:
                return current_extra_data_addr
            current_extra_data_addr = idc.Qword(current_extra_data_addr + BSExtraData.Offset.PtrNext)
            if current_extra_data_addr == 0:
                return None

    class Offset(Enum):
        vftable     = 0x00
        PtrNext     = 0x08
        field_10    = 0x10
        Type        = 0x12

class StringCache(MemObject):
    def __init__(self, addr, deepness = 0):
        super(StringCache, self).__init__(addr, deepness)

    class Entry(object):
        class Offset(Enum):
            PtrNext             = 0
            State               = 0x08
            Length              = 0x0C
            PtrExternDataEntry  = 0x10
            PtrData             = 0x18
    class Ref(object):
        class Offset(Enum):
            Entry = 0

BSFixedString = StringCache.Entry

class ExtraTextDisplayData(BSExtraData):
    def __init__(self, addr, deepness = 0):
        super(ExtraTextDisplayData, self).__init__(addr, deepness)

    class Offset(Enum):
        Name                = 0x18
        PtrMessage          = 0x20
        PtrQuest            = 0x28
        Type                = 0x30
        PtrTextReplaceData  = 0x38
        NameLength          = 0x40

class ExtraDataList(MemObject):
    def __init__(self, addr, deepness = 0):
        super(ExtraDataList, self).__init__(addr, deepness)
        extraDataAddr = idc.Qword(addr + ExtraDataList.Offset.PtrBSExtraData.value)
        if extraDataAddr == 0:
            self.ExtraData = NullObject()
        else:
            if deepness >= max_deepness:
                self.ExtraData = extraDataAddr
            else:
                self.ExtraData = BSExtraData(extraDataAddr, deepness + 1)
                extraDataTypes = {
                    0x99: ExtraTextDisplayData
                }
                extraDataType = extraDataTypes.get(self.ExtraData.Type, BSExtraData)
                if (extraDataType != BSExtraData):
                    self.ExtraData = extraDataType(addr, deepness + 1)

    def __repr__(self):
        extra = ConditionalFormat(self.ExtraData)
        return ("<ExtraDataList at 0x{:X}, extraData: " + extra.format + ">").format(self.addr, extra.repr)

    def toArray(self):
        if type(self.ExtraData) == NullObject:
            return []
        return self.ExtraData.toArray()

    def printExtraDataTypes(self):
        for extraData in self.toArray():
            print(extraData.getTypeName())

    def getExtraDataByType(self, extraDataType):
        # lock is skipped
        return self.ExtraData.getExtraDataByType(extraDataType)

    class Offset(Enum):
        PtrBSExtraData = 0x08

class BaseFormComponent(MemObject):
    def __init__(self, addr, deepness = 0):
        super(BaseFormComponent, self).__init__(addr, deepness)
        self.vftable = idc.Qword(addr + BaseFormComponent.Offset.vftable)

    class Offset(Enum):
        vftable = 0

class TESForm(MemObject):
    def __init__(self, addr, deepness = 0):
        super(TESForm, self).__init__(addr, deepness)
        self.formType = idc.Byte(addr + TESForm.Offset.FormType.value)
        self.flags = idc.Dword(addr + TESForm.Offset.Flags.value)
        self.formId = idc.Dword(addr + TESForm.Offset.FormId.value)

    def getVFTable(self):
        return VFTable(idc.Qword(self.addr), self.deepness + 1)
    
    def getName(self, max_length=None):
        name = None
        try:
            func_ea = idaapi.get_imagebase() + int('0x1599B0', 16)
            name = idc.GetString(idaapi.Appcall.proto(func_ea, "PVOID __fastcall TESFullName::possibly_getItemFullNameValue (PVOID inptr);")(self.addr).value)
        except:
            if pdbg: traceback.print_exc()

        if name is None:
            name = '<unknown>'

        return name

    def __repr__(self):
        typeName = "<unknown>"
        try:
            vftable = self.getVFTable()
            typeName = vftable.RTTICompleteObjectLocator.RTTITypeDescriptor.name
        except:
            if pdbg: traceback.print_exc()
        
        return "<TESForm at 0x{:X}, type: 0x{:X}, flags: 0x{:X}, formId: {:X} name: {}, typeName: {}>".format(self.addr, self.formType, self.flags, self.formId, self.getName(), typeName)

    class Offset(Enum):
        Flags = 0x10
        FormId = 0x14
        FormType = 0x1A

class TESFullName(BaseFormComponent):
    def __init__(self, addr, deepness = 0):
        super(TESFullName, self).__init__(addr, deepness)
        fixedStringAddr = addr + TESFullName.Offset.Name.value
        if deepness >= max_deepness:
            self.Name = fixedStringAddr
        else:
            self.Name = BSFixedString(fixedStringAddr, deepness + 1)

    class Offset(Enum):
        Name = 0x08

class Stack(MemObject):
    def __init__(self, addr, deepness = 0):
        super(Stack, self).__init__(addr, deepness)
        nextStackAddr = idc.Qword(addr + Stack.Offset.PtrNextStack.value)
        if nextStackAddr == 0:
            self.NextStack = NullObject()
        else:
            if deepness >= max_deepness:
                self.NextStack = nextStackAddr
            else:
                self.NextStack = Stack(nextStackAddr, deepness + 1)
        extraDataListAddr = idc.Qword(addr + Stack.Offset.PtrExtraDataList.value)
        if extraDataListAddr == 0:
            self.ExtraDataList = NullObject()
        else:
            if deepness >= max_deepness:
                self.ExtraDataList = extraDataListAddr
            else:
                self.ExtraDataList = ExtraDataList(extraDataListAddr, deepness + 1)
        self.count = idc.Dword(addr + Stack.Offset.Count.value)
        self.flags = idc.Byte(addr + Stack.Offset.Flags.value)

    def __repr__(self):
        stack = ConditionalFormat(self.NextStack)
        extra = ConditionalFormat(self.ExtraDataList)

        try:
            return ("<Stack at 0x{:X}, count: {}, nextStack: " + stack.format + ", extraDataList: " + extra.format + ">").format(self.addr, self.count, stack.repr, extra.repr)
        except:
            if pdbg: traceback.print_exc()
            return "<error>"

    def hasNextStack(self):
        return type(self.NextStack) != NullObject

    def hasExtraDataList(self):
        return type(self.ExtraDataList) != NullObject

    def toArray(self):
        deepness = 0
        deepness_max = 10
        result = [self]
        current = self
        while deepness < deepness_max:
            if current.hasNextStack():
                current = self.NextStack
                result.append(current)
            else:
                break
            deepness = deepness + 1
        return result

    def isEquipped(self):
        return self.flags & Stack.Flags.IsEquipped.value != 0

    class Flags(Enum):
        IsEquipped = 0x7

    class Offset(Enum):
        PtrNextStack        = 0x10
        PtrExtraDataList    = 0x18
        Count               = 0x20
        Flags               = 0x24

class BGSInventoryItem(MemObject):
    def __init__(self, addr, deepness = 0):
        super(BGSInventoryItem, self).__init__(addr, deepness)

        formAddr = idc.Qword(addr + BGSInventoryItem.Offset.form.value)
        stackAddr = idc.Qword(addr + BGSInventoryItem.Offset.stack.value)

        if stackAddr == 0:
            self.stack = NullObject()
        else:
            if deepness >= max_deepness:
                self.stack = stackAddr
            else:
                self.stack = Stack(stackAddr, deepness + 1)
        
        if formAddr == 0:
            self.form = NullObject()
        else:
            if deepness >= max_deepness:
                self.form = formAddr
            else:
                self.form = TESForm(formAddr, deepness + 1)

    def __repr__(self):
        form = ConditionalFormat(self.form if self.deepness >= max_deepness else self.form.addr)
        stack = ConditionalFormat(self.stack if self.deepness >= max_deepness else self.stack.addr)
        
        return ("<BGSInventoryItem at 0x{:X}, TESForm: " + form.format + ", Stack: " + stack.format + ", Name: {}>").format(self.addr, form.repr, stack.repr, self.getName(12))


    class Offset(Enum):
        form = 0
        stack = 8

    def getName(self, max_length=None):
        itemName = None
        # this block can be completely replaced with:
        #itemName = idc.GetString(Appcall.proto("TESFullName::possibly_getItemFullNameValue", "PVOID __fastcall TESFullName::possibly_getItemFullNameValue (PVOID inptr);")(0x0000000103C3BAB8).value)
        #.text:00000001401599B0 TESFullName::possibly_getItemFullNameValue proc near

        # TODO: move to a separate library file
        try:
            dynamic_cast = idaapi.Appcall.proto("msvcrt__RTDynamicCast", "PVOID __fastcall __RTDynamicCast (PVOID inptr, LONG VfDelta, PVOID SrcType, PVOID TargetType, BOOL isReference);")
        except:
            # sometimes it has two underscore symbols, sometimes three
            dynamic_cast = idaapi.Appcall.proto("msvcrt___RTDynamicCast", "PVOID __fastcall __RTDynamicCast (PVOID inptr, LONG VfDelta, PVOID SrcType, PVOID TargetType, BOOL isReference);")

        tes_full_name_ptr = dynamic_cast(self.form if self.deepness >= max_deepness else self.form.addr, 0, 0x00000001436CB140, 0x00000001436CE220, 0).value

        if (tes_full_name_ptr != 0):
            func_ea = idaapi.get_imagebase() + int("0x52980", 16)
            get_full_name_cstr = idaapi.Appcall.proto(func_ea, "PVOID __fastcall TESFullName::get_name_cstr (PVOID inptr);")
            strAddr = get_full_name_cstr(tes_full_name_ptr).value
            if strAddr != 0:
                itemName = idc.GetString(strAddr)
                if itemName is not None:
                    if max_length is not None:
                        itemName = (itemName[:12] + '..') if len(itemName) > 75 else itemName

        if itemName is None:
            itemName = '<unknown>'
        return itemName

class TArray(MemObject):
    def __init__(self, addr, t_type = None, t_size = None, deepness = 0):
        super(TArray, self).__init__(addr, deepness)
        self.capacity = idc.Dword(addr + TArray.Offset.Capacity.value)
        self.count = idc.Dword(addr + TArray.Offset.Count.value)
        self.maxEntries = 300
        self.t_type = t_type

        self.entriesAddr = idc.Qword(addr + TArray.Offset.Entries.value)
        if t_type is None:
            self.Entries = NullObject()
        else:
            if deepness >= max_deepness:
                self.Entries = self.entriesAddr
            else:
                if (self.count <= 0) or (t_type is None) or (t_size is None):
                    self.Entries = []
                else:
                    self.Entries = [t_type(i, deepness + 1) for i in range(self.entriesAddr, self.entriesAddr + t_size * (self.count if self.count < self.maxEntries else self.maxEntries), t_size)]

    def __repr__(self):
        type_name = "<unknown>" if self.t_type is None else self.t_type.__name__
        return "<tArray at 0x%X, Entries: 0x%X, count: %s, capacity: %s, type: %s>" % (self.addr, self.entriesAddr, self.count, self.capacity, type_name)

    class Offset(Enum):
        Entries = 0 # heap array of T
        Capacity = 0x8
        Count = 0x10

class BGSInventoryList(MemObject):
    def __init__(self, addr, deepness = 0):
        super(BGSInventoryList, self).__init__(addr, deepness)
        self.weight = idc.GetFloat(addr + BGSInventoryList.Offset.Weight.value)
        inventoryItemsAddr = addr + BGSInventoryList.Offset.Items.value
        if (deepness >= max_deepness):
            self.Items = inventoryItemsAddr
        else:
            self.Items = TArray(inventoryItemsAddr, BGSInventoryItem, 16, deepness + 1)
    
    def __repr__(self):
        count = 0
        items = ConditionalFormat(self.Items)
        try:
            count = self.Items.count
        except:
            if pdbg: traceback.print_exc()
        return ("<BGSInventoryList at 0x{:X}, weight: {}, count: {}, items: "+ items.format + ">").format(self.addr, self.weight, count, items.repr)

    class Offset(Enum):
        Items   = 0x58 # TArray<BGSInventoryItem>
        Weight  = 0x70 # float (4 bytes)

class TESObjectREFR(TESForm):
    def __init__(self, addr, deepness = 0):
        super(TESObjectREFR, self).__init__(addr, deepness)
        inventoryListAddr = idc.Qword(addr + TESObjectREFR.Offset.InventoryList.value)

        if (deepness >= max_deepness):
            self.InventoryList = inventoryListAddr
        else:
            self.InventoryList = BGSInventoryList(inventoryListAddr, deepness + 1)
    
    def __repr__(self):
        return "<TESObjectREFR at 0x%X, BGSInventoryList: 0x%X, Form:\n  %s>" % (self.addr, self.InventoryList.addr, super(TESObjectREFR, self).__repr__())

    class Offset(Enum):
        InventoryList = 0xF8

class VFTable(MemObject):
    def __init__(self, addr, deepness = 0):
        super(VFTable, self).__init__(addr, deepness)

        ptrRttiCol = self.addr + VFTable.Offset.RTTICompleteObjectLocator.value
        rttiCOLAddr = idc.Qword(ptrRttiCol)
        if pdbg: print("COLp: 0x%X" % (ptrRttiCol))
        if pdbg: print("COL : 0x%X" % (rttiCOLAddr))

        if deepness >= max_deepness:
            self.RTTICompleteObjectLocator = rttiCOLAddr
        else:
            self.RTTICompleteObjectLocator = RTTICompleteObjectLocator(rttiCOLAddr, deepness + 1)

        # short names
        self.col = self.RTTICompleteObjectLocator

    def __repr__(self):
        name = self.RTTICompleteObjectLocator.RTTITypeDescriptor.name
        return "<VFTable at 0x%X, COL: 0x%X, Name: %s>" % (self.addr, self.RTTICompleteObjectLocator.addr, name)
    
    class Offset(Enum):
        RTTICompleteObjectLocator = - 0x8   # 0x8

class RTTICompleteObjectLocator(MemObject):
    def __init__(self, addr, deepness = 0):
        super(RTTICompleteObjectLocator, self).__init__(addr, deepness)
        
        self.thisOffset = idc.Dword(self.addr + RTTICompleteObjectLocator.Offset.this.value)
        self.ctorDisplacement = idc.Dword(self.addr + RTTICompleteObjectLocator.Offset.ctorDisplacement.value)
        descriptorAddr = RVA(idc.Dword(self.addr + RTTICompleteObjectLocator.Offset.rvaTypeDescriptor.value))
        hierarchyAddr = RVA(idc.Dword(self.addr + RTTICompleteObjectLocator.Offset.rvaTypeHierarchy.value))
        if pdbg: print("RTD: 0x%X" % (descriptorAddr))
        if pdbg: print("RTH: 0x%X" % (hierarchyAddr))

        if deepness >= max_deepness:
            self.RTTITypeDescriptor = descriptorAddr
            self.RTTIClassHierarchyDescriptor = hierarchyAddr
        else:
            self.RTTITypeDescriptor = RTTITypeDescriptor(descriptorAddr, deepness + 1)
            self.RTTIClassHierarchyDescriptor = RTTIClassHierarchyDescriptor(hierarchyAddr, deepness + 1)

        # TODO: add self.ObjectBase

        #short names
        self.rtd = self.RTTITypeDescriptor
        self.rhd = self.RTTIClassHierarchyDescriptor

    class Offset(Enum):
        signature           = 0x00  # 0x4
        this                = 0x04  # 0x4
        ctorDisplacement    = 0x08  # 0x4
        rvaTypeDescriptor   = 0x0C  # 0x4
        rvaTypeHierarchy    = 0x10  # 0x4
        rvaObjectBase       = 0x14  # 0x4

class RTTITypeDescriptor(MemObject):
    def __init__(self, addr, deepness = 0):
        super(RTTITypeDescriptor, self).__init__(addr, deepness)
        nameAddr = addr + RTTITypeDescriptor.Offset.mangledName.value + RTTITypeDescriptor.NameOffset.classPrefix.value
        if pdbg: print("NAM: 0x%X" % (nameAddr))
        self.mangledName = idc.GetString(nameAddr)
        demangledName = idc.Demangle('??_7' + self.mangledName + '6B@', 8)
        if demangledName != None:
            demangledName = demangledName[0:len(demangledName)-11]
        self.name = demangledName

    def __repr__(self):
        return "<RTTITypeDescriptor at 0x%X, NAM: %s>" % (self.addr, self.name)

    class Offset(Enum):
        typeInfo            = 0x00  # 0x8
        internalRuntimeRef  = 0x08  # 0x8
        mangledName         = 0x10

    class NameOffset(Enum):
        classPrefix         = 0x4   # skips "class" prefix

class RTTIClassHierarchyDescriptor(MemObject):
    def __init__(self, addr, deepness = 0):
        super(RTTIClassHierarchyDescriptor, self).__init__(addr, deepness)

        signatureAddr = addr + RTTIClassHierarchyDescriptor.Offset.signature.value
        attributesAddr = addr + RTTIClassHierarchyDescriptor.Offset.attributes.value
        numberOfItemsAddr = addr + RTTIClassHierarchyDescriptor.Offset.numberOfItems.value
        baseClassHierarchyArrAddr = RVA(idc.Dword(addr + RTTIClassHierarchyDescriptor.Offset.rvaBaseClassArrRef.value))

        if pdbg: print("BCA: 0x%X" % (baseClassHierarchyArrAddr))

        self.signature = idc.Dword(signatureAddr)
        self.attributes = idc.Dword(attributesAddr)
        self.numberOfItems = idc.Dword(numberOfItemsAddr)
        self.baseClassHierarchyArray = baseClassHierarchyArrAddr

    def __repr__(self):
        return "<RTTITypeHierarchy at 0x%X, SIG: 0x%X, ATT: 0x%X, NUM: 0x%X>" % (self.addr, self.signature, self.attributes, self.numberOfItems)

    def getChildren(self, max_children = 50):
        # iterate over Base Class Array
        children = [] 
        if self.numberOfItems > max_children:
            return children

        # 0-th child is reference to self
        for i in range(1, self.numberOfItems + 1):
            baseClassDescriptorAddr = RVA(idc.Dword(self.baseClassHierarchyArray + i * 4))
            baseClassDescriptor = None
            if self.deepness >= max_deepness:
                baseClassDescriptor = baseClassDescriptorAddr
            else:
                baseClassDescriptor = RTTIBaseClassDescriptor(baseClassDescriptorAddr, self.deepness + 1)
            children.append(baseClassDescriptor)
        return children

    def printChildren(self):
        print("Children:")
        # iterate over Base Class Array
        for baseClassDescriptor in self.getChildren():
            print(" - %s" % (baseClassDescriptor.typeDescriptor))

    def hasChildren(self):
        return self.numberOfItems > 0

    class Offset(Enum):
        signature          = 0x00  # 0x4
        attributes         = 0x04  # 0x4
        numberOfItems      = 0x08  # 0x4
        rvaBaseClassArrRef = 0x0C  # 0x4

class RTTIBaseClassDescriptor(MemObject):
    def __init__(self, addr, deepness = 0):
        super(RTTIBaseClassDescriptor, self).__init__(addr, deepness)
        typeDescriptorAddr = RVA(idc.Dword(addr + RTTIBaseClassDescriptor.Offset.rvaTypeDescriptor.value))
        classHierarchyAddr = RVA(idc.Dword(addr + RTTIBaseClassDescriptor.Offset.rvaClassHierarchy.value))

        if pdbg: print("BCD : %X" % (typeDescriptorAddr))
        if pdbg: print("BCHD: %X" % (classHierarchyAddr))

        self.numberOfSubElements = idc.Dword(addr + RTTIBaseClassDescriptor.Offset.numOfSubElements.value)
        self.memberDisplacement = idc.Dword(addr + RTTIBaseClassDescriptor.Offset.memberDisplacement.value)
        self.vftableDisplacement = idc.Dword(addr + RTTIBaseClassDescriptor.Offset.vftableDisplacement.value)
        self.displacementWithinVFTable = idc.Dword(addr + RTTIBaseClassDescriptor.Offset.displacementWithinVFTable.value)
        self.baseClassAttributes = idc.Dword(addr + RTTIBaseClassDescriptor.Offset.baseClassAttributes.value)

        if deepness >= max_deepness:
            self.typeDescriptor = typeDescriptorAddr
            self.classHierarchyDescriptor = classHierarchyAddr
        else:
            self.typeDescriptor = RTTITypeDescriptor(typeDescriptorAddr, deepness + 1)
            self.classHierarchyDescriptor = RTTIClassHierarchyDescriptor(classHierarchyAddr, deepness + 1)
    
    def __repr__(self):
        return "<RTTIBaseClassDescriptor at 0x%X, RTD: 0x%X, NUM: 0x%s, MDS: 0x%s, VDS: 0x%s, DWV: 0x%s, BAT: 0x%X, BHD: 0x%X>" % (self.addr, self.typeDescriptor.addr, self.numberOfSubElements, self.memberDisplacement, self.vftableDisplacement, self.displacementWithinVFTable, self.baseClassAttributes, self.classHierarchyDescriptor.addr)

    def hasChildren(self):
        return self.numberOfSubElements > 0

    class Offset(Enum):
        rvaTypeDescriptor           = 0x00 # 0x4
        numOfSubElements            = 0x04 # 0x4
        memberDisplacement          = 0x08 # 0x4
        vftableDisplacement         = 0x0C # 0x4
        displacementWithinVFTable   = 0x10 # 0x4
        baseClassAttributes         = 0x14 # 0x4
        rvaClassHierarchy           = 0x18 # 0x4