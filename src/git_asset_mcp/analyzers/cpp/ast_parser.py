"""C / C++ / CUDA analysis via tree-sitter-cpp.

Extracts the same ``FileAnalysis`` shape as the Python analyzer:
  - symbols: functions / methods / classes / structs / namespaces (+ module),
  - imports: ``#include`` edges,
  - calls:   syntactic ``call_expression`` edges (all marked inferred=True —
             C++ has no import-graph resolvable statically at this stage).

Only syntactic facts are emitted; no semantic resolution is attempted.
"""
from __future__ import annotations

from git_asset_mcp.analyzers.models import CallEdge, FileAnalysis, ImportEdge, ParseError, Symbol

_CASTS = {"static_cast", "dynamic_cast", "reinterpret_cast", "const_cast"}

_PARSER = None


def _get_parser():
    """Lazily build the tree-sitter parser (first call imports the grammar)."""
    global _PARSER
    if _PARSER is None:
        import tree_sitter
        import tree_sitter_cpp

        lang_obj = tree_sitter_cpp.language()
        try:
            # tree-sitter >= 0.25 accepts the language object directly.
            _PARSER = tree_sitter.Parser(lang_obj)
        except TypeError:
            _PARSER = tree_sitter.Parser(tree_sitter.Language(lang_obj))
    return _PARSER


def _module_name_from_path(path: str) -> str:
    p = path.replace("\\", "/").rstrip("/")
    for suffix in (".cuh", ".hpp", ".hxx", ".cpp", ".cxx", ".cc", ".c", ".h", ".cu", ".hh", ".c++"):
        if p.endswith(suffix):
            p = p[: -len(suffix)]
            break
    return p.replace("/", ".").strip(".") or "<module>"


def _node_text(node) -> str:
    return node.text.decode("utf-8", "ignore") if node else ""


def _child(node, *types: str):
    for c in node.children:
        if c.type in types:
            return c
    return None


#: Node types that are purely declarator containers — recurse through them.
_DECLARATOR_TYPES = {
    "declarator", "function_declarator", "pointer_declarator", "reference_declarator",
    "field_declarator", "qualified_identifier", "template_function", "parenthesized_declarator",
    "init_declarator", "attribute_declarator", "abstract_function_declarator",
}
#: Node types that carry the actual name.
_NAME_TYPES = {"identifier", "field_identifier", "type_identifier", "operator_name",
               "destructor_name", "template_type"}


def _function_name(node) -> str:
    """Extract the function/method name from a function_definition node."""
    decl = _child(node, "declarator", "function_declarator")
    if decl is None:
        return ""
    name = _name_from_declarator(decl)
    if name is None:
        return ""
    # Out-of-class definition "Class::method" -> keep the method part only.
    return name.split("::")[-1]


def _name_from_declarator(node):
    """Walk a declarator subtree to its innermost name node.

    Returns the text of the name or ``None``. Never descends into the
    function body, parameters, or member initializer lists.
    """
    if node is None:
        return None
    t = node.type
    if t in _NAME_TYPES:
        return _node_text(node)
    if t in _DECLARATOR_TYPES:
        # qualified_identifier: a::b::name -> name
        if t == "qualified_identifier":
            return _node_text(node).split("::")[-1]
        for c in node.children:
            if c.type in _DECLARATOR_TYPES or c.type in _NAME_TYPES:
                name = _name_from_declarator(c)
                if name is not None:
                    return name
        return None
    return None


def _function_signature(node) -> tuple[str, list[str], str]:
    """Return (signature, params, return_annotation) for a function_definition."""
    name = _function_name(node)
    fd = _child(node, "function_declarator") or _child(node, "declarator")
    params: list[str] = []
    ret = ""
    if fd is not None:
        plist = _child(fd, "parameter_list") if fd.type == "function_declarator" else None
        plist = plist or _child(node, "parameter_list")
        if plist is not None:
            for p in plist.children:
                if p.type in ("parameter_declaration", "optional_parameter_declaration",
                              "variadic_parameter"):
                    params.append(p.text.decode("utf-8", "ignore").strip())
    type_node = _child(node, "type", "primitive_type", "template_type", "qualified_type",
                       "struct_specifier", "auto", "decltype")
    if type_node is not None:
        ret = type_node.text.decode("utf-8", "ignore").strip()
    sig = f"{ret} {name}({', '.join(params)})" if ret else f"{name}({', '.join(params)})"
    return sig, params, ret


class _Walker:
    def __init__(self, analysis: FileAnalysis, module_name: str):
        self._analysis = analysis
        self._module = module_name
        self._scope: list[str] = [module_name]
        self._scope_kinds: list[str] = ["module"]
        self._current_func = module_name

    @property
    def _current(self) -> str:
        return ".".join(self._scope)

    def _push(self, name: str, kind: str) -> None:
        self._scope.append(name)
        self._scope_kinds.append(kind)

    def _pop(self) -> None:
        if len(self._scope) > 1:
            self._scope.pop()
            self._scope_kinds.pop()

    def walk(self, node) -> None:
        t = node.type
        if t == "preproc_include":
            self._add_include(node)
        elif t == "namespace_definition":
            self._add_namespace(node)
            return  # _add_namespace walks children itself
        elif t in ("class_specifier", "struct_specifier", "union_specifier"):
            self._add_class(node)
            return  # _add_class walks children itself
        elif t == "enum_specifier":
            self._add_enum(node)
        elif t == "function_definition":
            self._add_function(node)
            return  # _add_function walks children itself
        elif t == "call_expression":
            self._add_call(node)
        self._walk_children(node)

    def _walk_children(self, node) -> None:
        for c in node.children:
            self.walk(c)

    def _add_include(self, node) -> None:
        path = _child(node, "system_lib_string", "string_literal", "identifier")
        module = _node_text(path).strip("<>\"")
        if module:
            self._analysis.imports.append(
                ImportEdge(module=module, line=node.start_point.row + 1)
            )

    def _add_namespace(self, node) -> None:
        name = _node_text(_child(node, "identifier", "namespace_identifier")) or "<anon>"
        qname = f"{self._current}.{name}" if name != "<anon>" else self._current
        self._analysis.symbols.append(
            Symbol(
                qualified_name=qname,
                symbol_type="namespace",
                signature=f"namespace {name}",
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
            )
        )
        self._push(name, "namespace")
        self._walk_children(node)
        self._pop()

    def _add_class(self, node) -> None:
        kind = node.type.replace("_specifier", "")
        name = _node_text(_child(node, "type_identifier", "identifier"))
        if not name:
            self._walk_children(node)
            return
        bases = ""
        blist = _child(node, "base_class_clause", "base_class_list")
        if blist is not None:
            bases = _node_text(blist)
        qname = f"{self._current}.{name}"
        prefix = "class" if kind == "class" else "struct"
        self._analysis.symbols.append(
            Symbol(
                qualified_name=qname,
                symbol_type="class",
                signature=f"{prefix} {name}{' : ' + bases if bases else ''}",
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
            )
        )
        self._push(name, "class")
        self._walk_children(node)
        self._pop()

    def _add_enum(self, node) -> None:
        name = _node_text(_child(node, "type_identifier", "identifier")) or "<anon>"
        qname = f"{self._current}.{name}"
        self._analysis.symbols.append(
            Symbol(
                qualified_name=qname,
                symbol_type="enum",
                signature=f"enum {name}",
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
            )
        )

    def _add_function(self, node) -> None:
        sig, params, ret = _function_signature(node)
        name = _function_name(node)
        if not name:
            return
        qname = f"{self._current}.{name}"
        sym_type = "method" if self._scope_kinds[-1] == "class" else "function"
        self._analysis.symbols.append(
            Symbol(
                qualified_name=qname,
                symbol_type=sym_type,
                signature=sig,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                params=params,
                return_annotation=ret,
            )
        )
        prev = self._current_func
        self._current_func = qname
        self._walk_children(node)
        self._current_func = prev

    def _add_call(self, node) -> None:
        func = _child(node, "identifier", "field_identifier", "qualified_identifier",
                      "operator_name", "template_function", "pointer_expression",
                      "field_expression")
        if func is None:
            return
        callee = _node_text(func).strip()
        leaf = callee.split("::")[-1].split("<")[0].strip()
        if not callee or leaf in _CASTS:
            return
        self._analysis.calls.append(
            CallEdge(
                caller=self._current_func,
                callee=callee,
                line=node.start_point.row + 1,
                inferred=True,
            )
        )


def analyze_source(source: str, path: str = "<unknown>") -> FileAnalysis:
    """Parse C/C++/CUDA source and extract symbols, includes and calls."""
    parser = _get_parser()
    try:
        tree = parser.parse(bytes(source, "utf-8"))
    except Exception as exc:  # tree-sitter raises on binary garbage
        raise ParseError(f"parse failed: {exc}") from exc

    module_name = _module_name_from_path(path)
    analysis = FileAnalysis(path=path)
    analysis.symbols.append(
        Symbol(
            qualified_name=module_name,
            symbol_type="module",
            signature=module_name,
            start_line=1,
            end_line=len(source.splitlines()) or 1,
        )
    )
    _Walker(analysis, module_name).walk(tree.root_node)
    return analysis
