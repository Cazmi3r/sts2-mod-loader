"""Minimal parser for Valve's text KeyValues format (.vdf / .acf)."""

import re

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*')


def loads(text):
    stack = [{}]
    key = None
    for m in _TOKEN.finditer(text):
        string, brace = m.group(1), m.group(2)
        if string is not None:
            string = string.replace('\\"', '"').replace("\\\\", "\\")
            if key is None:
                key = string
            else:
                stack[-1][key] = string
                key = None
        elif brace == "{":
            child = {}
            stack[-1][key if key is not None else ""] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
    return stack[0]


def load(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return loads(f.read())
