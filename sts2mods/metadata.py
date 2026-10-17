"""A small, read-only reader for .NET assembly metadata (ECMA-335), in pure Python.

Only what's needed to answer: "which types/methods/fields does this mod reference in
another assembly, and do they still exist there?"
"""

import re
import struct

# Table numbers
MODULE, TYPEREF, TYPEDEF, FIELDPTR, FIELD, METHODPTR, METHODDEF, PARAMPTR, PARAM = range(9)
INTERFACEIMPL, MEMBERREF, CONSTANT, CUSTOMATTRIBUTE, FIELDMARSHAL, DECLSECURITY = range(9, 15)
CLASSLAYOUT, FIELDLAYOUT, STANDALONESIG, EVENTMAP, EVENTPTR, EVENT, PROPERTYMAP = range(15, 22)
PROPERTYPTR, PROPERTY, METHODSEMANTICS, METHODIMPL, MODULEREF, TYPESPEC, IMPLMAP = range(22, 29)
FIELDRVA, ENCLOG, ENCMAP, ASSEMBLY, ASSEMBLYPROCESSOR, ASSEMBLYOS, ASSEMBLYREF = range(29, 36)
ASSEMBLYREFPROCESSOR, ASSEMBLYREFOS, FILE, EXPORTEDTYPE, MANIFESTRESOURCE = range(36, 41)
NESTEDCLASS, GENERICPARAM, METHODSPEC, GENERICPARAMCONSTRAINT = range(41, 45)

CODED = {
    "TypeDefOrRef": (2, [TYPEDEF, TYPEREF, TYPESPEC]),
    "HasConstant": (2, [FIELD, PARAM, PROPERTY]),
    "HasCustomAttribute": (5, [METHODDEF, FIELD, TYPEREF, TYPEDEF, PARAM, INTERFACEIMPL, MEMBERREF,
                               MODULE, DECLSECURITY, PROPERTY, EVENT, STANDALONESIG, MODULEREF,
                               TYPESPEC, ASSEMBLY, ASSEMBLYREF, FILE, EXPORTEDTYPE, MANIFESTRESOURCE,
                               GENERICPARAM, GENERICPARAMCONSTRAINT, METHODSPEC]),
    "HasFieldMarshal": (1, [FIELD, PARAM]),
    "HasDeclSecurity": (2, [TYPEDEF, METHODDEF, ASSEMBLY]),
    "MemberRefParent": (3, [TYPEDEF, TYPEREF, MODULEREF, METHODDEF, TYPESPEC]),
    "HasSemantics": (1, [EVENT, PROPERTY]),
    "MethodDefOrRef": (1, [METHODDEF, MEMBERREF]),
    "MemberForwarded": (1, [FIELD, METHODDEF]),
    "Implementation": (2, [FILE, ASSEMBLYREF, EXPORTEDTYPE]),
    "CustomAttributeType": (3, [None, None, METHODDEF, MEMBERREF, None]),
    "ResolutionScope": (2, [MODULE, MODULEREF, ASSEMBLYREF, TYPEREF]),
    "TypeOrMethodDef": (1, [TYPEDEF, METHODDEF]),
}

# Column kinds: int = fixed byte width, "s" string heap, "g" guid heap, "b" blob heap,
# ("i", table) simple index, ("c", name) coded index.
SCHEMA = {
    MODULE: [("Generation", 2), ("Name", "s"), ("Mvid", "g"), ("EncId", "g"), ("EncBaseId", "g")],
    TYPEREF: [("Scope", ("c", "ResolutionScope")), ("Name", "s"), ("Namespace", "s")],
    TYPEDEF: [("Flags", 4), ("Name", "s"), ("Namespace", "s"), ("Extends", ("c", "TypeDefOrRef")),
              ("FieldList", ("i", FIELD)), ("MethodList", ("i", METHODDEF))],
    FIELDPTR: [("Field", ("i", FIELD))],
    FIELD: [("Flags", 2), ("Name", "s"), ("Signature", "b")],
    METHODPTR: [("Method", ("i", METHODDEF))],
    METHODDEF: [("RVA", 4), ("ImplFlags", 2), ("Flags", 2), ("Name", "s"), ("Signature", "b"),
                ("ParamList", ("i", PARAM))],
    PARAMPTR: [("Param", ("i", PARAM))],
    PARAM: [("Flags", 2), ("Sequence", 2), ("Name", "s")],
    INTERFACEIMPL: [("Class", ("i", TYPEDEF)), ("Interface", ("c", "TypeDefOrRef"))],
    MEMBERREF: [("Class", ("c", "MemberRefParent")), ("Name", "s"), ("Signature", "b")],
    CONSTANT: [("Type", 2), ("Parent", ("c", "HasConstant")), ("Value", "b")],
    CUSTOMATTRIBUTE: [("Parent", ("c", "HasCustomAttribute")), ("Type", ("c", "CustomAttributeType")),
                      ("Value", "b")],
    FIELDMARSHAL: [("Parent", ("c", "HasFieldMarshal")), ("NativeType", "b")],
    DECLSECURITY: [("Action", 2), ("Parent", ("c", "HasDeclSecurity")), ("PermissionSet", "b")],
    CLASSLAYOUT: [("PackingSize", 2), ("ClassSize", 4), ("Parent", ("i", TYPEDEF))],
    FIELDLAYOUT: [("Offset", 4), ("Field", ("i", FIELD))],
    STANDALONESIG: [("Signature", "b")],
    EVENTMAP: [("Parent", ("i", TYPEDEF)), ("EventList", ("i", EVENT))],
    EVENTPTR: [("Event", ("i", EVENT))],
    EVENT: [("Flags", 2), ("Name", "s"), ("EventType", ("c", "TypeDefOrRef"))],
    PROPERTYMAP: [("Parent", ("i", TYPEDEF)), ("PropertyList", ("i", PROPERTY))],
    PROPERTYPTR: [("Property", ("i", PROPERTY))],
    PROPERTY: [("Flags", 2), ("Name", "s"), ("Type", "b")],
    METHODSEMANTICS: [("Semantics", 2), ("Method", ("i", METHODDEF)), ("Association", ("c", "HasSemantics"))],
    METHODIMPL: [("Class", ("i", TYPEDEF)), ("Body", ("c", "MethodDefOrRef")),
                 ("Declaration", ("c", "MethodDefOrRef"))],
    MODULEREF: [("Name", "s")],
    TYPESPEC: [("Signature", "b")],
    IMPLMAP: [("Flags", 2), ("Member", ("c", "MemberForwarded")), ("ImportName", "s"),
              ("ImportScope", ("i", MODULEREF))],
    FIELDRVA: [("RVA", 4), ("Field", ("i", FIELD))],
    ENCLOG: [("Token", 4), ("FuncCode", 4)],
    ENCMAP: [("Token", 4)],
    ASSEMBLY: [("HashAlgId", 4), ("Major", 2), ("Minor", 2), ("Build", 2), ("Revision", 2), ("Flags", 4),
               ("PublicKey", "b"), ("Name", "s"), ("Culture", "s")],
    ASSEMBLYPROCESSOR: [("Processor", 4)],
    ASSEMBLYOS: [("PlatformId", 4), ("Major", 4), ("Minor", 4)],
    ASSEMBLYREF: [("Major", 2), ("Minor", 2), ("Build", 2), ("Revision", 2), ("Flags", 4),
                  ("PublicKeyOrToken", "b"), ("Name", "s"), ("Culture", "s"), ("HashValue", "b")],
    ASSEMBLYREFPROCESSOR: [("Processor", 4), ("AssemblyRef", ("i", ASSEMBLYREF))],
    ASSEMBLYREFOS: [("PlatformId", 4), ("Major", 4), ("Minor", 4), ("AssemblyRef", ("i", ASSEMBLYREF))],
    FILE: [("Flags", 4), ("Name", "s"), ("HashValue", "b")],
    EXPORTEDTYPE: [("Flags", 4), ("TypeDefId", 4), ("Name", "s"), ("Namespace", "s"),
                   ("Implementation", ("c", "Implementation"))],
    MANIFESTRESOURCE: [("Offset", 4), ("Flags", 4), ("Name", "s"), ("Implementation", ("c", "Implementation"))],
    NESTEDCLASS: [("Nested", ("i", TYPEDEF)), ("Enclosing", ("i", TYPEDEF))],
    GENERICPARAM: [("Number", 2), ("Flags", 2), ("Owner", ("c", "TypeOrMethodDef")), ("Name", "s")],
    METHODSPEC: [("Method", ("c", "MethodDefOrRef")), ("Instantiation", "b")],
    GENERICPARAMCONSTRAINT: [("Owner", ("i", GENERICPARAM)), ("Constraint", ("c", "TypeDefOrRef"))],
}

PRIMITIVES = {
    0x01: "void", 0x02: "bool", 0x03: "char", 0x04: "sbyte", 0x05: "byte", 0x06: "short",
    0x07: "ushort", 0x08: "int", 0x09: "uint", 0x0A: "long", 0x0B: "ulong", 0x0C: "float",
    0x0D: "double", 0x0E: "string", 0x16: "TypedReference", 0x18: "nint", 0x19: "nuint", 0x1C: "object",
}


class BadImage(Exception):
    pass


class Assembly:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = data = f.read()
        self.path = path
        try:
            self._parse_pe()
            self._parse_metadata()
        except (struct.error, IndexError, KeyError) as e:
            raise BadImage(f"{path}: {e}") from e
        self._typedef_names = None

    # ---- PE / metadata plumbing -------------------------------------------------------

    def _parse_pe(self):
        d = self.data
        if d[:2] != b"MZ":
            raise BadImage("not a PE file")
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        if d[pe:pe + 4] != b"PE\0\0":
            raise BadImage("no PE signature")
        nsec, = struct.unpack_from("<H", d, pe + 6)
        opt_size, = struct.unpack_from("<H", d, pe + 20)
        opt = pe + 24
        magic, = struct.unpack_from("<H", d, opt)
        dirs = opt + (96 if magic == 0x10B else 112)
        cli_rva, _ = struct.unpack_from("<II", d, dirs + 14 * 8)
        if not cli_rva:
            raise BadImage("not a .NET assembly")
        self.sections = []
        sec = opt + opt_size
        for k in range(nsec):
            vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", d, sec + k * 40 + 8)
            self.sections.append((vaddr, max(vsize, rawsize), rawptr))
        cli = self._rva(cli_rva)
        md_rva, _ = struct.unpack_from("<II", d, cli + 8)
        self.md = self._rva(md_rva)

    def _rva(self, rva):
        for vaddr, size, rawptr in self.sections:
            if vaddr <= rva < vaddr + size:
                return rva - vaddr + rawptr
        raise BadImage(f"RVA {rva:#x} outside sections")

    def _parse_metadata(self):
        d, md = self.data, self.md
        if struct.unpack_from("<I", d, md)[0] != 0x424A5342:
            raise BadImage("bad metadata signature")
        vlen, = struct.unpack_from("<I", d, md + 12)
        p = md + 16 + vlen
        nstreams, = struct.unpack_from("<H", d, p + 2)
        p += 4
        self.streams = {}
        for _ in range(nstreams):
            off, size = struct.unpack_from("<II", d, p)
            end = d.index(b"\0", p + 8)
            name = d[p + 8:end].decode("ascii")
            p = (end + 4) & ~3
            self.streams[name] = (md + off, size)
        tables_off = (self.streams.get("#~") or self.streams["#-"])[0]
        self.strings_off = self.streams["#Strings"][0]
        self.blob_off = self.streams["#Blob"][0] if "#Blob" in self.streams else None

        heap_sizes = d[tables_off + 6]
        self.str_w = 4 if heap_sizes & 1 else 2
        self.guid_w = 4 if heap_sizes & 2 else 2
        self.blob_w = 4 if heap_sizes & 4 else 2
        valid, = struct.unpack_from("<Q", d, tables_off + 8)
        p = tables_off + 24
        self.rows = [0] * 64
        for t in range(64):
            if valid >> t & 1:
                self.rows[t] = struct.unpack_from("<I", d, p)[0]
                p += 4
        if self.str_w == 4 or any(self.rows[t] for t in range(0x2D, 64)):
            pass  # extra (uncompressed / portable pdb) tables are not expected in game mods

        self.table_off, self.row_size, self.col_layout = {}, {}, {}
        for t in range(0x2D):
            if not valid >> t & 1:
                continue
            layout, off = [], 0
            for name, kind in SCHEMA[t]:
                w = self._width(kind)
                layout.append((name, off, w, kind))
                off += w
            self.table_off[t], self.row_size[t], self.col_layout[t] = p, off, layout
            p += off * self.rows[t]

    def _width(self, kind):
        if isinstance(kind, int):
            return kind
        if kind == "s":
            return self.str_w
        if kind == "g":
            return self.guid_w
        if kind == "b":
            return self.blob_w
        if kind[0] == "i":
            return 2 if self.rows[kind[1]] < 0x10000 else 4
        bits, tables = CODED[kind[1]]
        biggest = max((self.rows[t] for t in tables if t is not None), default=0)
        return 2 if biggest < (1 << (16 - bits)) else 4

    def row(self, table, index):
        """1-based row -> dict of raw column values (coded indices decoded to (table, row))."""
        base = self.table_off[table] + (index - 1) * self.row_size[table]
        out = {}
        for name, off, w, kind in self.col_layout[table]:
            v = struct.unpack_from("<H" if w == 2 else "<I" if w == 4 else "<B", self.data, base + off)[0] \
                if w in (1, 2, 4) else 0
            if isinstance(kind, tuple) and kind[0] == "c":
                bits, tables = CODED[kind[1]]
                tag = v & ((1 << bits) - 1)
                v = (tables[tag] if tag < len(tables) else None, v >> bits)
            elif kind == "s":
                v = self.string(v)
            out[name] = v
        return out

    def string(self, off):
        start = self.strings_off + off
        end = self.data.index(b"\0", start)
        return self.data[start:end].decode("utf-8", "replace")

    def blob(self, off):
        p = self.blob_off + off
        n, p = _read_compressed(self.data, p)
        return self.data[p:p + n]

    def count(self, table):
        return self.rows[table] if table in self.table_off else 0

    # ---- names --------------------------------------------------------------------------

    @property
    def name(self):
        return self.row(ASSEMBLY, 1)["Name"] if self.count(ASSEMBLY) else None

    def typedef_name(self, index):
        if self._typedef_names is None:
            enclosing = {}
            for i in range(1, self.count(NESTEDCLASS) + 1):
                r = self.row(NESTEDCLASS, i)
                enclosing[r["Nested"]] = r["Enclosing"]
            names = {}

            def build(i, depth=0):
                if i in names:
                    return names[i]
                r = self.row(TYPEDEF, i)
                if i in enclosing and depth < 20:
                    n = build(enclosing[i], depth + 1) + "/" + r["Name"]
                else:
                    n = (r["Namespace"] + "." if r["Namespace"] else "") + r["Name"]
                names[i] = n
                return n
            for i in range(1, self.count(TYPEDEF) + 1):
                build(i)
            self._typedef_names = names
        return self._typedef_names[index]

    def typeref_name(self, index, depth=0):
        r = self.row(TYPEREF, index)
        scope_t, scope_i = r["Scope"]
        if scope_t == TYPEREF and depth < 20:
            return self.typeref_name(scope_i, depth + 1) + "/" + r["Name"]
        return (r["Namespace"] + "." if r["Namespace"] else "") + r["Name"]

    def typeref_assembly(self, index, depth=0):
        r = self.row(TYPEREF, index)
        scope_t, scope_i = r["Scope"]
        if scope_t == TYPEREF and depth < 20:
            return self.typeref_assembly(scope_i, depth + 1)
        if scope_t == ASSEMBLYREF:
            return self.row(ASSEMBLYREF, scope_i)["Name"]
        return None

    def type_name(self, coded):
        t, i = coded
        if t == TYPEDEF:
            return self.typedef_name(i)
        if t == TYPEREF:
            return self.typeref_name(i)
        if t == TYPESPEC:
            return self.sig_type_string(self.blob(self.row(TYPESPEC, i)["Signature"]))
        return "?"

    # ---- signatures ---------------------------------------------------------------------

    def _tdor(self, data, p):
        v, p = _read_compressed(data, p)
        tag, idx = v & 3, v >> 2
        table = [TYPEDEF, TYPEREF, TYPESPEC][tag] if tag < 3 else None
        return (table, idx), p

    def _type(self, data, p):
        et = data[p]
        p += 1
        if et in PRIMITIVES:
            return PRIMITIVES[et], p
        if et in (0x1F, 0x20):                       # custom modifiers: skip
            _, p = self._tdor(data, p)
            return self._type(data, p)
        if et == 0x45:                               # pinned
            return self._type(data, p)
        if et == 0x0F:
            t, p = self._type(data, p)
            return t + "*", p
        if et == 0x10:
            t, p = self._type(data, p)
            return t + "&", p
        if et in (0x11, 0x12):
            c, p = self._tdor(data, p)
            return self.type_name(c), p
        if et in (0x13, 0x1E):
            n, p = _read_compressed(data, p)
            return ("!" if et == 0x13 else "!!") + str(n), p
        if et == 0x1D:
            t, p = self._type(data, p)
            return t + "[]", p
        if et == 0x14:
            t, p = self._type(data, p)
            rank, p = _read_compressed(data, p)
            nsizes, p = _read_compressed(data, p)
            for _ in range(nsizes):
                _, p = _read_compressed(data, p)
            nlo, p = _read_compressed(data, p)
            for _ in range(nlo):
                _, p = _read_compressed(data, p)
            return f"{t}[{',' * (rank - 1)}]", p
        if et == 0x15:
            p += 1                                   # CLASS / VALUETYPE
            c, p = self._tdor(data, p)
            n, p = _read_compressed(data, p)
            args = []
            for _ in range(n):
                a, p = self._type(data, p)
                args.append(a)
            return f"{self.type_name(c)}<{','.join(args)}>", p
        if et == 0x1B:
            s, p = self._method_sig(data, p)
            return "fnptr(" + s + ")", p
        raise BadImage(f"unknown element type {et:#x}")

    def _method_sig(self, data, p):
        conv = data[p]
        p += 1
        gen = ""
        if conv & 0x10:
            n, p = _read_compressed(data, p)
            gen = f"<{n}>"
        n, p = _read_compressed(data, p)
        ret, p = self._type(data, p)
        params = []
        for _ in range(n):
            if data[p] == 0x41:                      # sentinel (varargs)
                p += 1
            t, p = self._type(data, p)
            params.append(t)
        static = "" if conv & 0x20 else "static "
        return f"{static}{gen}({','.join(params)}){ret}", p

    def sig_type_string(self, data):
        try:
            return self._type(data, 0)[0]
        except (IndexError, BadImage):
            return "?"

    def member_sig(self, data):
        """Normalized signature string for a method or field signature blob."""
        try:
            if data[0] & 0x0F == 0x06:               # field
                return "field:" + self._type(data, 1)[0]
            return self._method_sig(data, 0)[0]
        except (IndexError, BadImage):
            return "?"


def _read_compressed(data, p):
    b = data[p]
    if b & 0x80 == 0:
        return b, p + 1
    if b & 0xC0 == 0x80:
        return ((b & 0x3F) << 8) | data[p + 1], p + 2
    return ((b & 0x1F) << 24) | (data[p + 1] << 16) | (data[p + 2] << 8) | data[p + 3], p + 4


def short_type(t):
    """'System.Collections.Generic.IEnumerable`1<MegaCrit.X.CardModel>' -> 'IEnumerable<CardModel>'."""
    t = re.sub(r"`\d+", "", t)
    t = re.sub(r"\b(?:[A-Za-z_]\w*\.)+(?=[A-Za-z_])", "", t)
    return t.replace("/", ".").replace("Nullable<", "Nullable<")


def pretty_sig(name, sig):
    """Readable 'Name(Param, Param) -> Return' from a normalized signature."""
    if sig.startswith("field:"):
        return f"{name} (field)"
    body = sig.replace("static ", "")
    if body.startswith("<"):
        body = body[body.index(">") + 1:]
    close = _matching_paren(body)
    params, ret = body[1:close], body[close + 1:]
    shown = ", ".join(short_type(p) for p in _split_params(params)) if params else ""
    ret = short_type(ret)
    return f"{name}({shown})" + (f" → {ret}" if ret and ret != "void" else "")


def _matching_paren(s):
    depth = 0
    for k, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return k
    return len(s) - 1


def _split_params(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out
