#!/usr/bin/env python3
"""Decompile a Scratch SB3 project.json into executable Python modules.

Subcommands:
  extract   Emit human-readable pseudo-code per sprite (debugging aid)
  parse     Build intermediate-representation JSON (ir.json)
  emit      Generate Python modules from ir.json
  all       Full pipeline: parse + emit (default)

Usage:
  decompile project.json                  # parse + emit (default)
  decompile project.json extract -o txt/
  decompile project.json parse -o ir.json
  decompile project.json emit -o decompiled/
"""
import argparse
import json
import logging
import os
import re
import sys
import math as _math_mod
from collections import defaultdict
from pathlib import Path

log = logging.getLogger("decompile")


# ---------------------------------------------------------------------------
# opcode tables  (shared by text-extract and IR builder)
# ---------------------------------------------------------------------------

SUBSTACKS = {
    "control_if":            ["CONDITION", "SUBSTACK"],
    "control_if_else":       ["CONDITION", "SUBSTACK", "SUBSTACK2"],
    "control_repeat":        ["TIMES", "SUBSTACK"],
    "control_repeat_until":  ["CONDITION", "SUBSTACK"],
    "control_while":         ["CONDITION", "SUBSTACK"],
    "control_for_each":      ["VARIABLE", "VALUE", "SUBSTACK"],
    "control_forever":       ["SUBSTACK"],
}

STMT_SIMPLE = {
    "data_setvariableto":       "set {VARIABLE} to %s",
    "data_changevariableby":    "change {VARIABLE} by %s",
    "data_addtolist":           "add %s to {LIST}",
    "data_deleteoflist":        "delete %s of {LIST}",
    "data_deletealloflist":     "delete all of {LIST}",
    "data_replaceitemoflist":   "replace item %s of {LIST} with %s",
    "data_insertatlist":        "insert %s at %s of {LIST}",
    "event_broadcast":          "broadcast %s",
    "event_broadcastandwait":   "broadcast %s and wait",
    "motion_movesteps":         "move %s steps",
    "motion_gotoxy":            "go to x:%s y:%s",
    "motion_goto":              "go to %s",
    "motion_setx":              "set x to %s",
    "motion_sety":              "set y to %s",
    "motion_changexby":         "change x by %s",
    "motion_changeyby":         "change y by %s",
    "motion_turnright":         "turn cw %s degrees",
    "motion_turnleft":          "turn ccw %s degrees",
    "motion_pointindirection":  "point in direction %s",
    "motion_pointtowards":      "point towards %s",
    "motion_setrotationstyle":  "set rotation style %s",
    "looks_say":                "say %s",
    "looks_sayforsecs":         "say %s for %s secs",
    "looks_think":              "think %s",
    "looks_thinkforsecs":       "think %s for %s secs",
    "looks_switchcostumeto":    "switch costume to %s",
    "looks_nextcostume":        "next costume",
    "looks_switchbackdropto":   "switch backdrop to %s",
    "looks_nextbackdrop":       "next backdrop",
    "looks_show":               "show",
    "looks_hide":               "hide",
    "looks_setsizeto":          "set size to %s",
    "looks_changeeffectby":     "change %s effect by %s",
    "looks_seteffectto":        "set %s effect to %s",
    "looks_cleareffects":       "clear graphic effects",
    "looks_gotofrontback":      "go to %s layer",
    "looks_goforwardbackwardlayers": "go %s %s layers",
    "pen_clear":                "pen clear",
    "pen_penUp":                "pen up",
    "pen_penDown":              "pen down",
    "pen_stamp":                "pen stamp",
    "pen_setPenColorToColor":   "set pen color to %s",
    "pen_setPenSizeTo":         "set pen size to %s",
    "pen_setPenColorParamTo":   "set pen %s to %s",
    "pen_changePenColorParamBy": "change pen %s by %s",
    "pen_setPenColorToNum":     "set pen color to %s",
    "control_wait":             "wait %s secs",
    "control_wait_until":       "wait until %s",
    "control_stop":             "stop %s",
    "control_start_as_clone":   "(start as clone)",
    "control_create_clone_of":  "create clone of %s",
    "control_delete_this_clone": "delete this clone",
    "sound_play":               "play sound %s",
    "sound_playuntildone":      "play sound %s until done",
    "sound_stopallsounds":      "stop all sounds",
    "sound_cleareffects":       "clear sound effects",
    "sound_setvolumeto":        "set volume to %s",
    "sound_changevolumeby":     "change volume by %s",
    "sensing_setdragmode":      "set drag mode %s",
    "sensing_resettimer":       "reset timer",
}

REPORTER_SIMPLE = {
    "operator_add":             "(%s + %s)",
    "operator_subtract":        "(%s - %s)",
    "operator_multiply":        "(%s * %s)",
    "operator_divide":          "(%s / %s)",
    "operator_mod":             "(%s mod %s)",
    "operator_round":           "round(%s)",
    "operator_mathop":          "%s(%s)",
    "operator_join":            "join(%s, %s)",
    "operator_letter_of":       "letter(%s) of %s",
    "operator_length":          "length(%s)",
    "operator_contains":        "contains(%s, %s)",
    "operator_and":             "(%s and %s)",
    "operator_or":              "(%s or %s)",
    "operator_not":             "(not %s)",
    "operator_eq":              "(%s = %s)",
    "operator_equals":          "(%s = %s)",
    "operator_lt":              "(%s < %s)",
    "operator_gt":              "(%s > %s)",
    "operator_random":          "random(%s, %s)",
    "data_itemoflist":          "item(%s) of {LIST}",
    "data_itemnumoflist":       "item # of %s in {LIST}",
    "data_lengthoflist":        "length of {LIST}",
    "data_listcontainsitem":    "list contains %s in {LIST}",
    "motion_xposition":         "(x position)",
    "motion_yposition":         "(y position)",
    "motion_direction":         "(direction)",
    "sensing_mousex":           "(mouse x)",
    "sensing_mousey":           "(mouse y)",
    "sensing_keypressed":       "(key %s pressed?)",
    "sensing_mousedown":        "(mouse down?)",
    "sensing_timer":            "(timer)",
    "sensing_distanceto":       "(distance to %s)",
    "sensing_touchingcolor":    "(touching color %s)",
    "sensing_coloristouchingcolor": "(color %s touching %s)",
    "sensing_current":          "(current %s)",
    "looks_size":               "(size)",
    "looks_costumenumbername":  "(costume %s)",
    "looks_backdropnumbername": "(backdrop %s)",
    "sound_volume":             "(volume)",
}

EVENT_HATS = {
    "event_whenflagclicked":           "when green flag clicked",
    "event_whenbroadcastreceived":     "when I receive %s",
    "event_whenkeypressed":            "when %s key pressed",
    "event_whenstageclicked":          "when stage clicked",
    "event_whenbackdropswitchesto":    "when backdrop switches to %s",
    "control_start_as_clone":          "when I start as a clone",
    "event_whenthisspriteclicked":     "when this sprite clicked",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def field_name(block, field):
    val = block.get("fields", {}).get(field)
    return val[0] if isinstance(val, list) else val


def sanitize(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", str(name))


def pyname(name):
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(name))
    if not s or s[0].isdigit():
        s = "_" + s
    return s


def _literal(val):
    if val is None:
        return 0.0
    if isinstance(val, list):
        t = val[0]
        # Scratch typed literal:
        #   4/5/8/9/11 = numeric (value already a number)
        #   10 = number-as-string
        #   7 = string (may look numeric)
        #   12/13 = variable/list ref (not literal)
        #   6/14 = broadcast
        if t in (4, 5, 6, 8, 9, 11):
            v = val[1]
            if isinstance(v, str):
                try:
                    return int(v) if v.lstrip('-').isdigit() else float(v)
                except (ValueError, TypeError):
                    pass
            return v
        if t in (7, 10):
            v = val[1]
            if isinstance(v, str):
                try:
                    return int(v) if v.lstrip('-').isdigit() else float(v)
                except (ValueError, TypeError):
                    pass
            return v
        if t == 14:
            return ("__BROADCAST__", val[1])
        return val
    if isinstance(val, str):
        try:
            return int(val) if val.lstrip('-').isdigit() else float(val)
        except (ValueError, TypeError):
            pass
    return val


# ===================================================================
#  IR PARSER  (project.json → structured IR dict)
# ===================================================================

class IRParser:
    """Parse a Scratch project.json dict into a structured IR."""

    def __init__(self, target, broadcasts):
        self.target = target
        self.blocks = {k: v for k, v in target["blocks"].items() if isinstance(v, dict)}
        self.broadcasts = broadcasts
        self.var_names = {vid: v[0] for vid, v in target.get("variables", {}).items()}
        self.list_names = {lid: li[0] for lid, li in target.get("lists", {}).items()}

    def parse_value(self, inp):
        if inp is None:
            return {"kind": "const", "v": 0.0}
        if not isinstance(inp, list):
            return {"kind": "const", "v": inp}
        kind = inp[0]
        if kind == 1:
            v = inp[1]
            if isinstance(v, str) and v in self.blocks:
                return self.parse_block(v)
            lit = _literal(v)
            return {"kind": "const", "v": lit}
        if kind == 2:
            return self._parse_ref(inp[1])
        if kind == 3:
            ref = inp[1]
            if isinstance(ref, str):
                if ref in self.blocks:
                    return self.parse_block(ref)
                # plain variable-name reference (e.g. item (var) of list)
                return {"kind": "var", "name": ref}
            if isinstance(ref, list):
                return self._parse_ref(ref)
            shadow = inp[2] if len(inp) > 2 else None
            lit = _literal(shadow)
            return {"kind": "const", "v": lit}
        return {"kind": "const", "v": 0.0}

    def _parse_ref(self, ref):
        if isinstance(ref, str):
            return self.parse_block(ref)
        if isinstance(ref, list):
            t = ref[0]
            if t == 12:
                # variable reference: ref[1] is the variable ID, map to name
                vid = ref[1]
                return {"kind": "var", "name": self.var_names.get(vid, vid)}
            if t == 13:
                # list reference: ref[1] is the list ID, map to name
                lid = ref[1]
                return {"kind": "list", "name": self.list_names.get(lid, lid)}
            if t in (6, 14):
                return {"kind": "const", "v": ("__BROADCAST__", ref[1])}
        return {"kind": "const", "v": 0.0}

    def parse_block(self, ref):
        if not isinstance(ref, str) or ref not in self.blocks:
            return {"kind": "const", "v": 0.0}
        b = self.blocks[ref]
        op = b.get("opcode", "")
        inp = b.get("inputs", {})
        fld = b.get("fields", {})

        if op == "data_variable":
            return {"kind": "var", "name": fld["VARIABLE"][0]}
        if op == "data_listcontents":
            return {"kind": "list", "name": fld["LIST"][0]}
        if op in ("argument_reporter_string_number", "argument_reporter_boolean"):
            return {"kind": "arg", "name": fld["VALUE"][0]}
        if op in ("math_number", "math_whole_number", "math_positive_number"):
            raw = fld.get("NUM", [0])[0]
            try:
                num = int(raw) if isinstance(raw, str) and raw.lstrip('-').isdigit() else float(raw)
            except (ValueError, TypeError):
                num = 0.0
            return {"kind": "const", "v": num}
        if op == "procedures_prototype":
            return {"kind": "const", "v": 0.0}
        if op == "procedures_call":
            return self.parse_call(b)
        if op == "sensing_keypressed":
            return {"kind": "expr", "op": "key_pressed", "args": {"KEY": self.parse_value(inp.get("KEY_OPTION"))}}
        if op == "sensing_keyoptions":
            return {"kind": "const", "v": fld.get("KEY_OPTION", [""])[0]}
        if op == "sensing_of":
            return {"kind": "expr", "op": "sensing_of",
                    "fields": {"PROPERTY": fld.get("PROPERTY", [""])[0]},
                    "args": {"OBJECT": self.parse_value(inp.get("OBJECT"))}}
        if op == "sensing_of_object_menu":
            return {"kind": "const", "v": fld.get("OBJECT", [""])[0]}
        if op == "sensing_distancetomenu":
            return {"kind": "const", "v": fld.get("DISTANCETOMENU", [""])[0]}
        if op == "sensing_touchingobjectmenu":
            return {"kind": "const", "v": fld.get("TOUCHINGOBJECTMENU", [""])[0]}
        if op == "sound_sounds_menu":
            return {"kind": "const", "v": fld.get("SOUND_MENU", [""])[0]}
        if op == "pen_menu_colorParam":
            return {"kind": "const", "v": fld.get("colorParam", [""])[0]}
        if op == "control_create_clone_of_menu":
            return {"kind": "const", "v": fld.get("CLONE_OPTION", [""])[0]}
        if op == "operator_compare":
            return {"kind": "expr", "op": "=",
                    "args": [self.parse_value(inp.get("OPERAND1")),
                             self.parse_value(inp.get("OPERAND2"))]}
        args = {k: self.parse_value(v) for k, v in inp.items()}
        fields = {k: (v[0] if isinstance(v, list) else v) for k, v in fld.items()}
        return {"kind": "expr", "op": op, "args": args, "fields": fields}

    def parse_call(self, b):
        m = b.get("mutation", {})
        proccode = m.get("proccode", "")
        argids = json.loads(m.get("argumentids", "[]"))
        inputs = b.get("inputs", {})
        name = proccode
        args = []
        for aid in argids:
            if aid in inputs:
                args.append(self.parse_value(inputs[aid]))
            else:
                args.append(None)
        return {"kind": "call", "name": name, "args": args}

    def parse_stmt(self, ref):
        out = []
        cur = ref
        while cur:
            if not isinstance(cur, str) or cur not in self.blocks:
                break
            b = self.blocks[cur]
            op = b.get("opcode", "")
            inp = b.get("inputs", {})
            fld = b.get("fields", {})
            args = {k: self.parse_value(v) for k, v in inp.items()
                    if k not in ("SUBSTACK", "SUBSTACK2")}
            fields = {k: (v[0] if isinstance(v, list) else v) for k, v in fld.items()}

            node = {"op": op, "args": args, "fields": fields}
            if op == "procedures_call":
                call = self.parse_call(b)
                node["args"]["_args"] = call["args"]
                node["fields"]["PROCCODE"] = call["name"]
            elif "SUBSTACK" in inp:
                node["sub"] = self.parse_stmt(inp["SUBSTACK"][1])
            if "SUBSTACK2" in inp:
                node["sub2"] = self.parse_stmt(inp["SUBSTACK2"][1])
            if op == "control_for_each":
                node["args"]["VALUE"] = self.parse_value(inp.get("VALUE"))
            out.append(node)
            cur = b.get("next")
        return out

    def build(self):
        roots = [k for k, b in self.blocks.items() if b.get("parent") is None]
        procedures = []
        hats = []
        scripts = []
        for k in roots:
            b = self.blocks[k]
            op = b.get("opcode", "")
            if op == "procedures_definition":
                proto = b.get("inputs", {}).get("custom_block", [None, None])[1]
                name = "procedure"
                warp = False
                argnames = []
                if isinstance(proto, str) and proto in self.blocks:
                    p = self.blocks[proto]
                    pm = p.get("mutation", {})
                    raw = pm.get("proccode", "procedure")
                    argnames = json.loads(pm.get("argumentnames", "[]"))
                    warp = pm.get("warp") == "true"
                    name = raw
                # Skip comment-like procedures (// prefix or zero-width space names)
                if name.startswith("//") or "\u200b" in name:
                    continue
                body = self.parse_stmt(b.get("next")) if b.get("next") else []
                procedures.append({"name": name, "warp": warp, "args": argnames, "body": body})
            elif op in ("event_whenflagclicked", "event_whenbroadcastreceived",
                        "event_whenkeypressed", "event_whenstageclicked",
                        "event_whenbackdropswitchesto", "control_start_as_clone",
                        "event_whenthisspriteclicked"):
                event = {"type": op}
                if op == "event_whenbroadcastreceived":
                    event["broadcast"] = field_name(b, "BROADCAST_OPTION")
                if op == "event_whenkeypressed":
                    event["key"] = field_name(b, "KEY_OPTION")
                if op == "event_whenbackdropswitchesto":
                    event["backdrop"] = field_name(b, "BACKDROP")
                body = self.parse_stmt(b.get("next")) if b.get("next") else []
                hats.append({"event": event, "body": body})
            else:
                body = self.parse_stmt(k)
                if body:
                    scripts.append(body)
        return procedures, hats, scripts


# ===================================================================
#  IR OPTIMISATION PASSES  (provably correct, LLVM-style)
#  28 listed optimisations:
#   1. Constant folding                   2. Algebraic identity simplification
#   3. Strength reduction                 4. Boolean literal normalisation
#   5. Comparison canonicalisation        6. Commutativity-aware hashing (CSE)
#   7. Common subexpression elim (CSE)    8. Constant propagation
#   9. Dead expression elimination       10. No-op statement removal
#  11. Empty-block elimination           12. Dead-code after terminators
#  13. Redundant broadcast collapse      14. Duplicate stmt collapse
#  15. Unreachable script removal        16. Loop-invariant code motion (LICM)
#  17. Loop strength reduction           18. Infinite-loop simplification
#  19. Empty-loop deletion               20. Procedure inlining
#  21. Dead procedure elimination        22. Tail-call / redundant call elim
#  23. Argument default flattening       24. Unused variable/list elimination
#  25. Broadcast deduplication           26. Asset/constant hoisting (LICM)
#  27. CFG simplification                28. Nested repeat collapse
# ===================================================================

_FOLDABLE_UNARY = frozenset({
    "operator_round", "operator_length", "operator_not",
})
_FOLDABLE_BINARY = frozenset({
    "operator_add", "operator_subtract", "operator_multiply",
    "operator_divide", "operator_mod", "operator_join",
    "operator_letter_of", "operator_contains",
    "operator_and", "operator_or",
    "operator_equals", "operator_eq", "operator_lt", "operator_gt",
})
_FOLDABLE_MATH = frozenset({
    "abs", "floor", "ceiling", "sqrt", "round", "pi", "e",
})
_PURE_OPS = frozenset({
    "operator_add", "operator_subtract", "operator_multiply",
    "operator_divide", "operator_mod", "operator_round",
    "operator_length", "operator_not", "operator_join",
    "operator_letter_of", "operator_contains",
    "operator_and", "operator_or",
    "operator_equals", "operator_eq", "operator_lt", "operator_gt",
    "operator_mathop",
})
_NOOP_STMT_OPS = frozenset({
    "motion_movesteps", "motion_turnright", "motion_turnleft",
    "motion_changexby", "motion_changeyby", "motion_setx", "motion_sety",
    "motion_gotoxy", "motion_pointindirection",
    "motion_goto", "motion_pointtowards", "motion_setrotationstyle",
    "looks_setsizeto", "looks_switchcostumeto", "looks_nextcostume",
    "looks_seteffectto", "looks_changeeffectby", "looks_cleareffects",
    "looks_show", "looks_hide",
    "data_setvariableto", "data_changevariableby",
    "data_addtolist", "data_deleteoflist", "data_deletealloflist",
    "data_replaceitemoflist", "data_insertatlist",
    "sound_setvolumeto", "sound_changevolumeby", "sound_stopallsounds",
    "pen_setPenColorToColor", "pen_setPenColorToNum", "pen_setPenSizeTo",
})


def _opt_num(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        try:
            return float(s)
        except ValueError:
            return 0.0
    return 0.0


def _opt_str(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else repr(v)
    return "" if v is None else str(v)


def _opt_eq(a, b):
    na, nb = _opt_num(a), _opt_num(b)
    if isinstance(a, (int, float, str)) and isinstance(b, (int, float, str)):
        try:
            float(_opt_str(a))
            float(_opt_str(b))
            return na == nb
        except ValueError:
            return _opt_str(a) == _opt_str(b)
    return na == nb


# ---- 1. Constant folding (improved) ------------------------------------

def _fold_expr(node):
    if not isinstance(node, dict):
        return node
    kind = node.get("kind")
    if kind != "expr":
        return node
    op = node.get("op", "")
    args = node.get("args", {})
    fields = node.get("fields", {})

    if isinstance(args, dict):
        folded_args = {k: _fold_expr(v) for k, v in args.items()}
    elif isinstance(args, list):
        folded_args = [_fold_expr(v) for v in args]
    else:
        folded_args = args

    def _all_const(d):
        items = d.items() if isinstance(d, dict) else enumerate(d)
        return all(
            isinstance(v, dict) and v.get("kind") == "const"
            and not (isinstance(v.get("v"), tuple) and v["v"][0] == "__BROADCAST__")
            for _, v in items
        )

    if op in _FOLDABLE_UNARY and _all_const(folded_args) and len(folded_args) >= 1:
        vals = list(folded_args.values())
        a = _opt_num(vals[0]["v"])
        if op == "operator_round":
            return {"kind": "const", "v": round(a)}
        if op == "operator_length":
            return {"kind": "const", "v": float(len(_opt_str(vals[0]["v"])))}
        if op == "operator_not":
            return {"kind": "const", "v": 1.0 if not a else 0.0}

    if op in _FOLDABLE_BINARY and _all_const(folded_args) and len(folded_args) >= 2:
        vals = list(folded_args.values())
        if op in ("operator_add", "operator_multiply", "operator_and", "operator_or"):
            acc = vals[0]["v"]
            for v in vals[1:]:
                vv = v["v"]
                na, nb = _opt_num(acc), _opt_num(vv)
                if op in ("operator_add",):
                    acc = na + nb
                elif op == "operator_multiply":
                    acc = na * nb
                elif op == "operator_and":
                    acc = 1.0 if bool(acc) and bool(vv) else 0.0
                elif op == "operator_or":
                    acc = 1.0 if bool(acc) or bool(vv) else 0.0
            return {"kind": "const", "v": acc}
        a, b = vals[0]["v"], vals[1]["v"]
        na, nb = _opt_num(a), _opt_num(b)
        if op == "operator_subtract":
            return {"kind": "const", "v": na - nb}
        if op == "operator_divide":
            return {"kind": "const", "v": 0.0 if nb == 0 else na / nb}
        if op == "operator_mod":
            return {"kind": "const", "v": 0.0 if nb == 0 else na % nb}
        if op == "operator_join":
            return {"kind": "const", "v": _opt_str(a) + _opt_str(b)}
        if op == "operator_letter_of":
            s, idx = _opt_str(a), int(nb)
            return {"kind": "const", "v": s[idx - 1] if 1 <= idx <= len(s) else ""}
        if op == "operator_contains":
            return {"kind": "const", "v": 1.0 if _opt_str(b) in _opt_str(a) else 0.0}
        if op in ("operator_equals", "operator_eq"):
            return {"kind": "const", "v": 1.0 if _opt_eq(a, b) else 0.0}
        if op == "operator_lt":
            return {"kind": "const", "v": 1.0 if na < nb else 0.0}
        if op == "operator_gt":
            return {"kind": "const", "v": 1.0 if na > nb else 0.0}

    if op == "operator_mathop" and _all_const(folded_args) and isinstance(folded_args, dict):
        oper = fields.get("OPERATOR", "")
        x = _opt_num(next(iter(folded_args.values()))["v"])
        if oper.lower() in _FOLDABLE_MATH:
            if oper.lower() == "pi":
                return {"kind": "const", "v": _math_mod.pi}
            if oper.lower() == "e":
                return {"kind": "const", "v": _math_mod.e}
            table = {"abs": abs, "floor": _math_mod.floor, "ceiling": _math_mod.ceil,
                     "sqrt": _math_mod.sqrt, "round": round}
            if oper.lower() in table:
                return {"kind": "const", "v": table[oper.lower()](x)}

    node = dict(node)
    node["args"] = folded_args
    return node


def _fold_body(body):
    out = []
    for s in body:
        s = dict(s)
        for key in ("args",):
            if key in s:
                if isinstance(s[key], dict):
                    s[key] = {k: _fold_expr(v) for k, v in s[key].items()}
                elif isinstance(s[key], list):
                    s[key] = [_fold_expr(v) for v in s[key]]
        for key in ("sub", "sub2"):
            if key in s and isinstance(s.get(key), list):
                s[key] = _fold_body(s[key])
        out.append(s)
    return out


# ---- 8. Constant propagation ---------------------------------------------

def _ir_const_prop_expr(node, knowns):
    if not isinstance(node, dict):
        return node
    kind = node.get("kind")
    if kind == "var":
        name = node.get("name", "")
        if name in knowns:
            return knowns[name]
        return node
    if kind == "expr":
        node = dict(node)
        if isinstance(node.get("args"), dict):
            node["args"] = {k: _ir_const_prop_expr(v, knowns) for k, v in node["args"].items()}
        elif isinstance(node.get("args"), list):
            node["args"] = [_ir_const_prop_expr(v, knowns) for v in node["args"]]
        return node
    if kind == "call":
        node = dict(node)
        if "args" in node:
            node["args"] = [_ir_const_prop_expr(v, knowns) for v in node["args"]]
        return node
    return node


def _expr_contains_call(node):
    """True if an expression tree contains a custom-reporter call (possible
    side effects on shared variables)."""
    if isinstance(node, list):
        return any(_expr_contains_call(v) for v in node)
    if isinstance(node, dict):
        if node.get("kind") == "call":
            return True
        a = node.get("args")
        if isinstance(a, dict):
            return any(_expr_contains_call(v) for v in a.values())
        if isinstance(a, list):
            return any(_expr_contains_call(v) for v in a)
    return False


# Statement ops that are "straight-line" and safe for const propagation. Any
# op not in this set (control flow, calls, broadcasts, waits, stops, clones,
# custom blocks, etc.) forces us to conservatively drop all known constants.
_CONST_PROP_SAFE_OPS = frozenset({
    "data_setvariableto", "data_changevariableby",
})


def _ir_const_prop(body, knowns=None):
    """Conservative straight-line constant propagation.

    Only propagates a constant from ``set [v] to (const)`` into later *reads*
    of ``v`` within the same straight-line run of statements. All known
    constants are dropped at any statement that could:
      * transfer control / yield (loops, ifs, waits) — another script may run,
      * call a custom block or broadcast — shared variables may change,
      * write ``v`` via a non-constant expression.
    This makes the pass safe even when variables are shared across sprites,
    because no other script can run between two adjacent straight-line
    statements with no yield point.
    """
    knowns = {} if knowns is None else dict(knowns)
    out = []
    for s in body:
        op = s.get("op", "")
        # Recurse into nested bodies with a CLEARED known-set (a loop/if body
        # may run multiple times or conditionally), then reset knowns after.
        if s.get("sub") is not None or s.get("sub2") is not None:
            s = dict(s)
            if s.get("sub") is not None:
                s["sub"], _ = _ir_const_prop(s["sub"], None)
            if s.get("sub2") is not None:
                s["sub2"], _ = _ir_const_prop(s["sub2"], None)
            out.append(s)
            knowns = {}
            continue

        if op not in _CONST_PROP_SAFE_OPS:
            # Unknown/unsafe statement: substitute reads first (safe), then
            # drop all knowns because control may leave straight-line flow.
            s = dict(s)
            s["args"] = {k: _ir_const_prop_expr(v, knowns) for k, v in s.get("args", {}).items()}
            out.append(s)
            knowns = {}
            continue

        s = dict(s)
        new_args = {k: _ir_const_prop_expr(v, knowns) for k, v in s.get("args", {}).items()}
        s["args"] = new_args
        var = s.get("fields", {}).get("VARIABLE", "")
        if op == "data_setvariableto":
            val = new_args.get("VALUE")
            if isinstance(val, dict) and val.get("kind") == "const" and not _expr_contains_call(val):
                knowns[var] = dict(val)
            else:
                knowns.pop(var, None)
        elif op == "data_changevariableby":
            knowns.pop(var, None)
        out.append(s)
    return out, knowns


# ---- 2–6. Expression simplification (algebraic, boolean, strength, commutativity, canonicalisation) ----

def _ir_simplify_expr(node):
    """Algebraic/boolean/strength reduction + canonicalisation."""
    if not isinstance(node, dict) or node.get("kind") != "expr":
        return node
    op = node.get("op", "")
    args = node.get("args", {})
    if isinstance(args, dict):
        args = {k: _ir_simplify_expr(v) for k, v in args.items()}
    elif isinstance(args, list):
        args = [_ir_simplify_expr(v) for v in args]

    def _is_const(v, *values):
        if not isinstance(v, dict) or v.get("kind") != "const":
            return False
        if values:
            return v.get("v") in values
        return True
    def _f0(v): return v in (0, 0.0)
    def _f1(v): return v in (1, 1.0)

    # ---- 5. Comparison canonicalisation: normalise constants to RHS ----
    if op in ("operator_add", "operator_multiply",
              "operator_equals", "operator_eq",
              "operator_and", "operator_or") and isinstance(args, dict):
        items = list(args.items())
        if len(items) == 2:
            k0, v0 = items[0]
            k1, v1 = items[1]
            c0 = _is_const(v0)
            c1 = _is_const(v1)
            if c0 and not c1:
                args = {k1: v1, k0: v0}
            elif op in ("operator_add", "operator_multiply") and isinstance(v0, dict) and isinstance(v1, dict):
                # Non-const left, const right already → keep; also sort arg keys for determinism
                if v0.get("kind") == "expr" and v1.get("kind") == "expr":
                    h0 = hash(str(v0)) if v0.get("kind") == "expr" else 0
                    h1 = hash(str(v1)) if v1.get("kind") == "expr" else 0
                    if h0 > h1:
                        args = {k1: v1, k0: v0}
                elif v0.get("kind") == "var" and v1.get("kind") == "var":
                    if v0.get("name", "") > v1.get("name", ""):
                        args = {k1: v1, k0: v0}
                elif v0.get("kind") == "var" and v1.get("kind") == "expr":
                    args = {k1: v1, k0: v0}  # var before expr

    if op in ("operator_lt", "operator_gt") and isinstance(args, dict):
        items = list(args.items())
        if len(items) == 2:
            k0, v0 = items[0]
            k1, v1 = items[1]
            if _is_const(v0) and not _is_const(v1):
                args = {k1: v1, k0: v0}
                op = "operator_lt" if op == "operator_gt" else "operator_gt"

    # ---- 6. Flatten nested associative ops ----
    if op in ("operator_add", "operator_multiply", "operator_and", "operator_or") and isinstance(args, dict):
        flat = []
        for v in args.values():
            if isinstance(v, dict) and v.get("kind") == "expr" and v.get("op") == op:
                flat.extend(v["args"].values())
            else:
                flat.append(v)
        if len(flat) != len(args):
            args = {str(i): v for i, v in enumerate(flat)}

    # ---- 2. Algebraic identities + 3. Strength reduction ----
    if op == "operator_add" and isinstance(args, dict):
        vals = list(args.values())
        const_zero = [i for i, v in enumerate(vals) if _is_const(v) and _f0(v.get("v"))]
        if const_zero:
            nz = [v for i, v in enumerate(vals) if i not in const_zero]
            if len(nz) == 0:
                return {"kind": "const", "v": 0.0}
            if len(nz) == 1:
                return nz[0]
            args = {str(i): v for i, v in enumerate(nz)}
        # x + x → 2*x
        if len(vals) == 2 and not _is_const(vals[0]) and not _is_const(vals[1]):
            if str(vals[0]) == str(vals[1]):
                return {"kind": "expr", "op": "operator_multiply",
                        "args": {"0": vals[0], "1": {"kind": "const", "v": 2.0}}, "fields": {}}

    if op == "operator_subtract" and isinstance(args, dict):
        vals = list(args.values())
        if len(vals) == 2:
            if _is_const(vals[1]) and _f0(vals[1].get("v")):
                return vals[0]
            if _is_const(vals[0]) and _f0(vals[0].get("v")):
                return {"kind": "expr", "op": "operator_multiply",
                        "args": {"0": vals[1], "1": {"kind": "const", "v": -1.0}}, "fields": {}}
            # x - x → 0
            if str(vals[0]) == str(vals[1]):
                return {"kind": "const", "v": 0.0}

    if op == "operator_multiply" and isinstance(args, dict):
        vals = list(args.values())
        # any * 0 → 0
        if any(_is_const(v) and _f0(v.get("v")) for v in vals):
            return {"kind": "const", "v": 0.0}
        const_one = [i for i, v in enumerate(vals) if _is_const(v) and _f1(v.get("v"))]
        if const_one:
            no = [v for i, v in enumerate(vals) if i not in const_one]
            if len(no) == 0:
                return {"kind": "const", "v": 1.0}
            if len(no) == 1:
                return no[0]
            args = {str(i): v for i, v in enumerate(no)}
        # strength: x * 2 → x + x
        if len(vals) == 2:
            c_idx = next((i for i, v in enumerate(vals) if _is_const(v)), None)
            if c_idx is not None:
                cv = vals[c_idx].get("v")
                nc = vals[1 - c_idx]
                if cv == 2.0:
                    return {"kind": "expr", "op": "operator_add",
                            "args": {"0": nc, "1": dict(nc)}, "fields": {}}

    if op == "operator_divide" and isinstance(args, dict):
        vals = list(args.values())
        if len(vals) == 2:
            if _is_const(vals[1]) and _f1(vals[1].get("v")):
                return vals[0]
            if vals[0] == vals[1] and not _is_const(vals[0]):
                return {"kind": "const", "v": 1.0}

    if op == "operator_mod" and isinstance(args, dict):
        vals = list(args.values())
        if len(vals) == 2:
            if _is_const(vals[1]) and _f1(vals[1].get("v")):
                return {"kind": "const", "v": 0.0}

    # ---- 4. Boolean literal normalisation ----
    if op == "operator_not" and isinstance(args, dict):
        vals = list(args.values())
        if len(vals) == 1:
            sub = vals[0]
            if _is_const(sub):
                return {"kind": "const", "v": 0.0 if sub.get("v") else 1.0}
            if isinstance(sub, dict) and sub.get("kind") == "expr":
                if sub.get("op") == "operator_not":
                    inner = sub.get("args", {})
                    inner_vals = list(inner.values())
                    if len(inner_vals) == 1:
                        return inner_vals[0]

    if op in ("operator_equals", "operator_eq") and isinstance(args, dict):
        vals = list(args.values())
        if len(vals) == 2:
            # x = x → true
            if str(vals[0]) == str(vals[1]):
                return {"kind": "const", "v": 1.0}

    if op in ("operator_lt", "operator_gt") and isinstance(args, dict):
        vals = list(args.values())
        if len(vals) == 2 and str(vals[0]) == str(vals[1]):
            return {"kind": "const", "v": 0.0}

    if op in ("operator_and", "operator_or") and isinstance(args, dict):
        vals = list(args.values())
        if len(vals) == 2:
            if op == "operator_and":
                if _is_const(vals[0]) and vals[0].get("v"):
                    return vals[1]
                if _is_const(vals[1]) and vals[1].get("v"):
                    return vals[0]
                if _is_const(vals[0]) and not vals[0].get("v"):
                    return vals[0]
                if _is_const(vals[1]) and not vals[1].get("v"):
                    return vals[1]
            if op == "operator_or":
                if _is_const(vals[0]) and vals[0].get("v"):
                    return vals[0]
                if _is_const(vals[1]) and vals[1].get("v"):
                    return vals[1]
                if _is_const(vals[0]) and not vals[0].get("v"):
                    return vals[1]
                if _is_const(vals[1]) and not vals[1].get("v"):
                    return vals[0]

    node = dict(node)
    node["args"] = args
    node["op"] = op
    return node


# ---- 7. Common subexpression elimination (CSE) --------------------------

# CSE hoists subexpressions into temporary variables named with _CSE_VAR_PREFIX.
# TurboWarp-compiled projects sometimes use "_cse0".."_cseN" as GLOBAL constant
# shadow variables (shared across procedures). To avoid colliding with those,
# the prefix is made collision-free per target (_cse_safe_prefix) and threaded
# through to the emitter, so CSE temps become module-locals (e.g. _cse_0) while
# real project variables stay sprite properties (sp['_cse0']). CSE is a pure
# optimization; it is safe to keep enabled because the prefix can never collide.
_ENABLE_CSE = True

_CSE_VAR_PREFIX = "_cse_"  # default; made collision-free per-target below


def _cse_safe_prefix(var_names):
    """Return a CSE temporary prefix that cannot collide with any real
    Scratch variable name in the project. CSE emits temps as Python locals
    (see the emitter), so a collision would silently corrupt values."""
    names = set(var_names or ())
    base = "_cse"
    # Guarantee NO real variable name starts with the returned prefix, so every
    # downstream startswith(cse_prefix) check reliably detects only temps. This
    # closes both the `_cse0` (real TurboWarp var) and `_cse_0` collision gaps.
    while True:
        prefix = base + "_"
        if not any(n.startswith(prefix) for n in names):
            return prefix
        base = base + "_"

def _hash_expr(node):
    """Deterministic hash for an expression tree (used for CSE)."""
    if isinstance(node, dict):
        kind = node.get("kind")
        if kind == "const":
            v = node.get("v")
            return ("const", v)
        if kind == "var":
            return ("var", node.get("name", ""))
        if kind == "arg":
            return ("arg", node.get("name", ""))
        if kind == "list":
            return ("list", node.get("name", ""))
        if kind == "call":
            args = tuple(_hash_expr(a) for a in node.get("args", []))
            return ("call", node.get("name", ""), args)
        if kind == "expr":
            args = node.get("args", {})
            if isinstance(args, dict):
                hashed = tuple(sorted((k, _hash_expr(v)) for k, v in args.items()))
            elif isinstance(args, list):
                hashed = tuple(_hash_expr(v) for v in args)
            else:
                hashed = ()
            fields = node.get("fields", {})
            fhash = tuple(sorted((k, str(v)) for k, v in fields.items()))
            return ("expr", node.get("op", ""), hashed, fhash)
        return (kind, str(node))
    return ("lit", node)


def _extract_var_names(node):
    """Return set of variable names referenced by an expression node."""
    names = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            if n.get("kind") == "var":
                names.add(n.get("name", ""))
            a = n.get("args") or {}
            if isinstance(a, dict):
                stack.extend(a.values())
            elif isinstance(a, list):
                stack.extend(a)
        elif isinstance(n, list):
            stack.extend(n)
    return names


def _ir_cse_block(body, var_base=None):
    if not _ENABLE_CSE:
        return body, 0
    if var_base is None:
        var_base = _CSE_VAR_PREFIX
    """Eliminate common subexpressions within a straight-line block.
    Two-pass: first count, then replace & hoist.
    Tracks variable writes to invalidate hoisted exprs that read modified vars.
    Only hoists pure (side-effect-free) ops.
    Returns (new_body, next_counter).
    """
    # Pass 1: count occurrences of every pure subexpr
    expr_count = defaultdict(int)
    def _count_exprs(node):
        if isinstance(node, dict):
            k = node.get("kind")
            if k == "expr" and node.get("op", "") in _PURE_OPS:
                h = _hash_expr(node)
                expr_count[h] += 1
            a = node.get("args") or {}
            if isinstance(a, dict):
                for val in a.values():
                    _count_exprs(val)
            elif isinstance(a, list):
                for val in a:
                    _count_exprs(val)
    for s in body:
        for v in s.get("args", {}).values():
            _count_exprs(v)
        if s.get("sub"):
            for ss in s["sub"]:
                _count_exprs(ss)
        if s.get("sub2"):
            for ss in s["sub2"]:
                _count_exprs(ss)

    # Only hoist subexprs that appear >= 2 times
    hoist_set = {h for h, c in expr_count.items() if c >= 2}
    # Assign counter per hash
    hoist_counter = {}
    next_ctr = [0]
    for h in hoist_set:
        hoist_counter[h] = next_ctr[0]
        next_ctr[0] += 1

    # Map hash → set of variable names read by that subexpr (for invalidation)
    hoist_vars = {}
    def _collect_hoist_vars(node):
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, dict):
                if n.get("kind") == "expr" and n.get("op", "") in _PURE_OPS:
                    h = _hash_expr(n)
                    if h in hoist_set and h not in hoist_vars:
                        hoist_vars[h] = _extract_var_names(n)
                a = n.get("args") or {}
                if isinstance(a, dict):
                    stack.extend(a.values())
                elif isinstance(a, list):
                    stack.extend(a)
            elif isinstance(n, list):
                stack.extend(n)
    for s in body:
        for v in s.get("args", {}).values():
            _collect_hoist_vars(v)

    # Pass 2: replace and hoist
    def _cse_in_expr(node, invalid):
        if isinstance(node, dict):
            k = node.get("kind")
            if k in ("var", "const", "arg", "list", "call"):
                return node
            if k == "expr":
                node = dict(node)
                inner_args = node.get("args", {})
                if isinstance(inner_args, dict):
                    node["args"] = {k: _cse_in_expr(v, invalid) for k, v in inner_args.items()}
                elif isinstance(inner_args, list):
                    node["args"] = [_cse_in_expr(v, invalid) for v in inner_args]
                h = _hash_expr(node)
                if h in hoist_set and h not in invalid:
                    var_name = f"{var_base}{hoist_counter[h]}"
                    return {"kind": "var", "name": var_name}
                return node
            return node
        return node

    out = []
    global_visited = set()
    # Track hoisted exprs invalidated by writes to referenced variables
    invalidated = set()
    for s in body:
        s = dict(s)
        # Check if this statement or its children write to variables
        written_vars = _licm_write_set([s])
        for written in written_vars:
            for h, vnames in list(hoist_vars.items()):
                if written in vnames:
                    hoist_set.discard(h)
                    invalidated.add(h)

        # Step 1: find first uses of hoisted subexprs (before replacement)
        first_uses = []
        def _find_first_uses(node):
            if isinstance(node, dict):
                k = node.get("kind")
                if k in ("var", "const", "arg", "list"):
                    return
                if k == "expr":
                    a2 = node.get("args") or {}
                    if isinstance(a2, dict):
                        for val in a2.values():
                            _find_first_uses(val)
                    elif isinstance(a2, list):
                        for val in a2:
                            _find_first_uses(val)
                    h = _hash_expr(node)
                    if h in hoist_set and h not in global_visited:
                        global_visited.add(h)
                        first_uses.append((h, node))
        a_vals = s.get("args", {})
        if isinstance(a_vals, dict):
            for v in a_vals.values():
                _find_first_uses(v)
        elif isinstance(a_vals, list):
            for v in a_vals:
                _find_first_uses(v)

        # Step 2: Insert set stmts for first uses
        pre = []
        for h, expr_node in first_uses:
            vn = f"{var_base}{hoist_counter[h]}"
            pre.append({
                "op": "data_setvariableto",
                "args": {"VALUE": expr_node},
                "fields": {"VARIABLE": vn},
            })

        # Step 3: Now replace all occurrences with var references
        s["args"] = {k: _cse_in_expr(v, invalidated) for k, v in s.get("args", {}).items()}
        if s.get("sub"):
            s["sub"], c = _ir_cse_block(s["sub"], var_base)
            next_ctr[0] = max(next_ctr[0], c)
        if s.get("sub2"):
            s["sub2"], c = _ir_cse_block(s["sub2"], var_base)
            next_ctr[0] = max(next_ctr[0], c)

        if pre:
            out.extend(pre)
        out.append(s)
    return out, next_ctr[0]


# ---- 9. Dead expression elimination (drop pure stmts that are no-ops) ----

_PURE_EMIT_EMPTY = frozenset({
    "operator_add", "operator_subtract", "operator_multiply",
    "operator_divide", "operator_mod", "operator_round",
    "operator_length", "operator_not", "operator_join",
    "operator_letter_of", "operator_contains",
    "operator_and", "operator_or",
    "operator_equals", "operator_eq", "operator_lt", "operator_gt",
    "operator_mathop",
})


def _is_pure_stmt(op):
    """True if the statement produces no side effects when executed."""
    return op in _PURE_EMIT_EMPTY


# ---- 10. No-op statement removal -----------------------------------------

def _is_noop_stmt(s):
    """Check if a statement is effectively a no-op."""
    op = s["op"]
    args = s.get("args", {})
    fields = s.get("fields", {})

    if op == "data_setvariableto":
        val = args.get("VALUE", {})
        if isinstance(val, dict) and val.get("kind") == "var":
            if val.get("name") == fields.get("VARIABLE", ""):
                return True
    if op == "data_changevariableby":
        val = args.get("VALUE", {})
        if isinstance(val, dict) and val.get("kind") == "const":
            v = val.get("v", 0)
            if v in (0, 0.0):
                return True
    if op == "motion_changexby":
        val = args.get("DX", {})
        if isinstance(val, dict) and val.get("kind") == "const":
            if val.get("v") in (0, 0.0):
                return True
    if op == "motion_changeyby":
        val = args.get("DY", {})
        if isinstance(val, dict) and val.get("kind") == "const":
            if val.get("v") in (0, 0.0):
                return True
    if op == "motion_turnright":
        val = args.get("DEGREES", {})
        if isinstance(val, dict) and val.get("kind") == "const":
            if val.get("v") in (0, 0.0):
                return True
    if op == "motion_turnleft":
        val = args.get("DEGREES", {})
        if isinstance(val, dict) and val.get("kind") == "const":
            if val.get("v") in (0, 0.0):
                return True
    if op == "motion_gotoxy":
        xv = args.get("X", {})
        yv = args.get("Y", {})
        if isinstance(xv, dict) and xv.get("kind") == "var" and isinstance(yv, dict) and yv.get("kind") == "var":
            if xv.get("name") == "x position" and yv.get("name") == "y position":
                return True
    if op == "looks_switchcostumeto":
        val = args.get("COSTUME", {})
        if isinstance(val, dict) and val.get("kind") == "var":
            if val.get("name") == "costume name":
                return True
    # pen_penUp / pen_penDown are NOT no-ops — they change pen state
    # which affects subsequent pen_move / pen_stamp behaviour.
    if op == "motion_movesteps":
        val = args.get("STEPS", {})
        if isinstance(val, dict) and val.get("kind") == "const":
            if val.get("v") in (0, 0.0):
                return True
    return False


# ---- 11. Empty-block elimination / 19. Empty-loop deletion -------------

def _is_empty_block(blk):
    """True if block contains only 'invisible' statements (no side effects)."""
    if not blk:
        return True
    # Ops that produce observable side effects (anything not pure data-flow)
    _SIDE_EFFECT_PREFIXES = (
        "motion_", "looks_", "sound_", "pen_", "sensing_",
        "event_", "control_create_clone_of", "control_delete_this_clone",
        "control_stop",
    )
    for s in blk:
        op = s.get("op", "")
        if op == "procedures_definition":
            continue
        if s.get("sub") or s.get("sub2"):
            return False
        if any(op.startswith(p) for p in _SIDE_EFFECT_PREFIXES):
            return False
        if op in ("data_setvariableto", "data_changevariableby",
                  "data_addtolist", "data_deleteoflist", "data_deletealloflist",
                  "data_replaceitemoflist", "data_insertatlist",
                  "procedures_call"):
            return False
    return True


# ---- 13. Redundant broadcast collapse / 14. Duplicate stmt collapse -----

def _collapse_duplicates(body):
    """Collapse consecutive duplicate statements and broadcast merges."""
    out = []
    for s in body:
        s = dict(s)
        if out:
            last = out[-1]
            lop = last.get("op")
            lop_args = last.get("args", {})
            lop_fields = last.get("fields", {})
            sop = s.get("op")
            sop_args = s.get("args", {})
            sop_fields = s.get("fields", {})
            # 14. Consecutive identical data_setvariableto (same var, same value)
            if lop == "data_setvariableto" and sop == "data_setvariableto":
                if lop_fields.get("VARIABLE") == sop_fields.get("VARIABLE"):
                    if str(lop_args.get("VALUE")) == str(sop_args.get("VALUE")):
                        continue
            # 14. Consecutive identical data_changevariableby (same var, same value)
            if lop == "data_changevariableby" and sop == "data_changevariableby":
                if lop_fields.get("VARIABLE") == sop_fields.get("VARIABLE"):
                    if str(lop_args.get("VALUE")) == str(sop_args.get("VALUE")):
                        continue
            # 14. Consecutive show/hide
            if lop == "looks_show" and sop == "looks_show":
                continue
            if lop == "looks_hide" and sop == "looks_hide":
                continue
            # 14. Consecutive identical pen up/down/clear (state changes)
            if lop == "pen_penDown" and sop == "pen_penDown":
                continue
            if lop == "pen_penUp" and sop == "pen_penUp":
                continue
            if lop == "pen_clear" and sop == "pen_clear":
                continue
            # 14. Consecutive identical pen color / size
            if lop == "pen_setPenColorToColor" and sop == "pen_setPenColorToColor":
                if str(lop_args.get("COLOR")) == str(sop_args.get("COLOR")):
                    continue
            if lop == "pen_setPenSizeTo" and sop == "pen_setPenSizeTo":
                if str(lop_args.get("SIZE")) == str(sop_args.get("SIZE")):
                    continue
            # 14. Consecutive identical set size / effect / volume
            if lop == "looks_setsizeto" and sop == "looks_setsizeto":
                if str(lop_args.get("SIZE")) == str(sop_args.get("SIZE")):
                    continue
            if lop == "looks_seteffectto" and sop == "looks_seteffectto":
                if lop_fields.get("EFFECT") == sop_fields.get("EFFECT") and \
                   str(lop_args.get("VALUE")) == str(sop_args.get("VALUE")):
                    continue
            if lop == "sound_setvolumeto" and sop == "sound_setvolumeto":
                if str(lop_args.get("VOLUME")) == str(sop_args.get("VOLUME")):
                    continue
            # 14. Consecutive identical set x / set y / go to xy
            if lop == "motion_setx" and sop == "motion_setx":
                if str(lop_args.get("X")) == str(sop_args.get("X")):
                    continue
            if lop == "motion_sety" and sop == "motion_sety":
                if str(lop_args.get("Y")) == str(sop_args.get("Y")):
                    continue
            if lop == "motion_gotoxy" and sop == "motion_gotoxy":
                if str(lop_args.get("X")) == str(sop_args.get("X")) and \
                   str(lop_args.get("Y")) == str(sop_args.get("Y")):
                    continue
            # 14. Consecutive identical switch costume / backdrop
            if lop == "looks_switchcostumeto" and sop == "looks_switchcostumeto":
                if str(lop_args.get("COSTUME")) == str(sop_args.get("COSTUME")):
                    continue
            if lop == "looks_switchbackdropto" and sop == "looks_switchbackdropto":
                if str(lop_args.get("BACKDROP")) == str(sop_args.get("BACKDROP")):
                    continue
            # 14. Consecutive clear effects / stop all sounds (no args)
            if lop == "looks_cleareffects" and sop == "looks_cleareffects":
                continue
            if lop == "sound_stopallsounds" and sop == "sound_stopallsounds":
                continue
            if lop == "sensing_resettimer" and sop == "sensing_resettimer":
                continue
            # 14. Consecutive identical point in direction / rotation style
            if lop == "motion_pointindirection" and sop == "motion_pointindirection":
                if str(lop_args.get("DIRECTION")) == str(sop_args.get("DIRECTION")):
                    continue
            if lop == "motion_setrotationstyle" and sop == "motion_setrotationstyle":
                if str(lop_args.get("STYLE")) == str(sop_args.get("STYLE")):
                    continue
            # 14. Consecutive identical pen color param / numeric pen color
            if lop == "pen_setPenColorParamTo" and sop == "pen_setPenColorParamTo":
                if lop_fields.get("colorParam") == sop_fields.get("colorParam") and \
                   str(lop_args.get("VALUE")) == str(sop_args.get("VALUE")):
                    continue
            if lop == "pen_setPenColorToNum" and sop == "pen_setPenColorToNum":
                if str(lop_args.get("COLOR")) == str(sop_args.get("COLOR")):
                    continue
            # 14. Consecutive identical go to front/back layer
            if lop == "looks_gotofrontback" and sop == "looks_gotofrontback":
                if str(lop_args.get("FRONT_BACK")) == str(sop_args.get("FRONT_BACK")):
                    continue
        if s.get("sub"):
            s["sub"] = _collapse_duplicates(s["sub"])
        if s.get("sub2"):
            s["sub2"] = _collapse_duplicates(s["sub2"])
        out.append(s)
    return out


# ---- 16. Loop-invariant code motion (LICM) -------------------------------

def _licm_write_set(body):
    """Return set of variable names written (data_setvariableto / data_changevariableby) in body."""
    writes = set()
    # Check if body contains any procedure call or broadcast - if so, be conservative
    def _has_side_effect(body):
        for s in body:
            op = s.get("op", "")
            if op in ("procedures_call", "event_broadcast", "event_broadcastandwait"):
                return True
            if s.get("sub") and _has_side_effect(s["sub"]):
                return True
            if s.get("sub2") and _has_side_effect(s["sub2"]):
                return True
        return False
    if _has_side_effect(body):
        # Can't determine which vars are written - return a sentinel to indicate all dirty
        return {"__ALL_DIRTY__"}
    for s in body:
        op = s.get("op", "")
        if op == "data_setvariableto":
            f = s.get("fields", {})
            v = f.get("VARIABLE", "")
            if v:
                writes.add(v)
        elif op == "data_changevariableby":
            f = s.get("fields", {})
            v = f.get("VARIABLE", "")
            if v:
                writes.add(v)
        if s.get("sub"):
            sub_writes = _licm_write_set(s["sub"])
            if "__ALL_DIRTY__" in sub_writes:
                return {"__ALL_DIRTY__"}
            writes |= sub_writes
        if s.get("sub2"):
            sub_writes = _licm_write_set(s["sub2"])
            if "__ALL_DIRTY__" in sub_writes:
                return {"__ALL_DIRTY__"}
            writes |= sub_writes
    return writes


_LOOP_HOISTABLE_OPS = _PURE_OPS | frozenset({
    "data_itemoflist", "data_lengthoflist", "data_listcontainsitem",
    "data_itemnumoflist",
})


def _expr_cost(node):
    if not isinstance(node, dict):
        return 0
    kind = node.get("kind")
    if kind == "const":
        return 0
    if kind in ("var", "arg", "list"):
        return 1
    if kind == "expr":
        args = node.get("args", {})
        if isinstance(args, dict):
            return 1 + sum(_expr_cost(v) for v in args.values())
        if isinstance(args, list):
            return 1 + sum(_expr_cost(v) for v in args)
        return 1
    if kind == "call":
        return 1 + sum(_expr_cost(a) for a in node.get("args", []))
    return 1


def _is_loop_invariant(node, write_set):
    if not isinstance(node, dict):
        return True
    kind = node.get("kind")
    if kind == "var":
        return node.get("name", "") not in write_set
    if kind in ("const", "arg", "list"):
        return True
    if kind == "expr":
        args = node.get("args", {})
        if isinstance(args, dict):
            return all(_is_loop_invariant(v, write_set) for v in args.values())
        if isinstance(args, list):
            return all(_is_loop_invariant(v, write_set) for v in args)
        return True
    if kind == "call":
        return all(_is_loop_invariant(a, write_set) for a in node.get("args", []))
    return False


def _licm_body(body, loop_depth=0, cse_prefix=None):
    """Hoist loop-invariant expressions out of loop bodies.

    Two phases:
      1. Hoist entire data_setvariableto statements with invariant RHS.
      2. Scan remaining statements for invariant sub-expressions (cost > 1)
         and replace them with temp variables named via cse_prefix.
    """
    if cse_prefix is None:
        cse_prefix = _CSE_VAR_PREFIX
    out = []
    for s in body:
        s = dict(s)
        if s.get("sub") and s["op"] in ("control_repeat", "control_repeat_until",
                                         "control_while", "control_forever", "control_for_each"):
            hoisted = []
            all_body_writes = _licm_write_set(s["sub"])
            if "__ALL_DIRTY__" in all_body_writes:
                s["sub"] = _licm_body(s["sub"], loop_depth + 1, cse_prefix)
                out.append(s)
                continue
            new_sub = []
            for ss in s["sub"]:
                ss = dict(ss)
                if ss["op"] == "data_setvariableto":
                    f = ss.get("fields", {})
                    target_var = f.get("VARIABLE", "")
                    val = ss.get("args", {}).get("VALUE", {})
                    if target_var and target_var not in all_body_writes:
                        if isinstance(val, dict) and val.get("kind") in ("const", "var"):
                            hoisted.append(ss)
                            continue
                        if isinstance(val, dict) and val.get("kind") == "expr" \
                                and val.get("op", "") in _LOOP_HOISTABLE_OPS:
                            if _is_loop_invariant(val, all_body_writes):
                                hoisted.append(ss)
                                continue
                new_sub.append(ss)
            licm_counter = [0]
            licm_seen = {}
            def _collect_invariant_subs(node):
                if not isinstance(node, dict):
                    return
                kind = node.get("kind")
                if kind == "expr" and node.get("op", "") in _LOOP_HOISTABLE_OPS:
                    if _is_loop_invariant(node, all_body_writes) and _expr_cost(node) > 1:
                        h = _hash_expr(node)
                        if h not in licm_seen:
                            licm_seen[h] = (f"{cse_prefix}l{licm_counter[0]}", dict(node))
                            licm_counter[0] += 1
                if kind == "expr":
                    for a in (node.get("args") or {}).values():
                        _collect_invariant_subs(a)
                elif kind == "call":
                    for a in node.get("args", []):
                        _collect_invariant_subs(a)
            for ss in new_sub:
                for v in ss.get("args", {}).values():
                    _collect_invariant_subs(v)
            def _replace_invariant(node):
                if not isinstance(node, dict):
                    return node
                kind = node.get("kind")
                if kind == "expr" and node.get("op", "") in _LOOP_HOISTABLE_OPS:
                    if _is_loop_invariant(node, all_body_writes) and _expr_cost(node) > 1:
                        h = _hash_expr(node)
                        if h in licm_seen:
                            return {"kind": "var", "name": licm_seen[h][0]}
                if kind == "expr":
                    args = node.get("args", {})
                    if isinstance(args, dict):
                        node = dict(node)
                        node["args"] = {k: _replace_invariant(v) for k, v in args.items()}
                    elif isinstance(args, list):
                        node = dict(node)
                        node["args"] = [_replace_invariant(v) for v in args]
                elif kind == "call":
                    node = dict(node)
                    node["args"] = [_replace_invariant(a) for a in node.get("args", [])]
                return node
            for i, ss in enumerate(new_sub):
                ss = dict(ss)
                ss["args"] = {k: _replace_invariant(v) for k, v in ss.get("args", {}).items()}
                new_sub[i] = ss
            for h, (var_name, orig_node) in licm_seen.items():
                hoisted.append({
                    "op": "data_setvariableto",
                    "args": {"VALUE": orig_node},
                    "fields": {"VARIABLE": var_name},
                })
            s["sub"] = _licm_body(new_sub, loop_depth + 1, cse_prefix)
            if hoisted and not s["sub"]:
                s["sub"] = [hoisted.pop()]
            if hoisted:
                out.extend(hoisted)
        else:
            if s.get("sub"):
                s["sub"] = _licm_body(s["sub"], loop_depth, cse_prefix)
            if s.get("sub2"):
                s["sub2"] = _licm_body(s["sub2"], loop_depth, cse_prefix)
        out.append(s)
    return out


# ---- 17. Loop strength reduction ---------------------------------------

_YIELD_INDUCING_OPS = frozenset({
    "control_wait", "control_wait_until",
    "motion_movesteps", "motion_gotoxy", "motion_goto",
    "motion_setx", "motion_sety", "motion_changexby", "motion_changeyby",
    "motion_turnright", "motion_turnleft", "motion_pointindirection",
    "motion_pointtowards", "motion_setrotationstyle",
    "motion_glideto", "motion_glidesecstoxy",
    "looks_switchcostumeto", "looks_nextcostume", "looks_switchbackdropto",
    "looks_nextbackdrop", "looks_sayforsecs", "looks_thinkforsecs",
    "sound_play", "sound_playuntildone",
    "sensing_askandwait", "sensing_touchingcolor", "sensing_coloristouchingcolor",
    "sensing_touchingobject", "sensing_loudness",
})


def _ir_loop_strength(body):
    """Unroll small constant-iteration loops; simplify do-nothing loops.
    Skips unrolling if the body contains yield-inducing ops (motion, wait, etc.)
    since unrolling would change timing semantics.
    """
    out = []
    for s in body:
        s = dict(s)
        if s.get("sub"):
            s["sub"] = _ir_loop_strength(s["sub"])
        if s.get("sub2"):
            s["sub2"] = _ir_loop_strength(s["sub2"])

        op = s["op"]
        if op == "control_repeat":
            times = s.get("args", {}).get("TIMES", {})
            if isinstance(times, dict) and times.get("kind") == "const":
                tv = times.get("v", 0)
                if isinstance(tv, (int, float)):
                    tv = int(tv)
                else:
                    try:
                        tv = int(tv)
                    except (ValueError, TypeError):
                        tv = 0
                if tv <= 0:
                    continue
                sub = s.get("sub", [])
                if tv <= 3 and len(sub) <= 10:
                    def _has_yield(block):
                        for stmt in block:
                            if stmt.get("op") in _YIELD_INDUCING_OPS:
                                return True
                            if stmt.get("sub") and _has_yield(stmt.get("sub", [])):
                                return True
                            if stmt.get("sub2") and _has_yield(stmt.get("sub2", [])):
                                return True
                        return False
                    if not _has_yield(sub):
                        unrolled = []
                        for _ in range(tv):
                            unrolled.extend(dict(ss) for ss in sub)
                        out.extend(unrolled)
                        continue
        out.append(s)
    return out


# ---- 20. Procedure inlining ---------------------------------------------

_inline_uid = [0]

def _inline_temp_name(uid, raw):
    """Build a valid Python-identifier temp name for an inlined proc arg.

    Scratch parameter names may contain spaces or punctuation (e.g. 'tri 1'),
    which are illegal in the local-variable identifiers these temps compile to.
    Both construction sites must agree, so route them through this helper.
    """
    safe = re.sub(r"\W", "_", str(raw))
    return f"_inline_{uid}_{safe}"

def _inline_procedures(procedures, hats):
    """Inline single-use procedures (called once from any hat/procedure)."""
    call_counts = defaultdict(list)
    for p in procedures:
        _count_calls(p["body"], p["name"], call_counts)
    for h in hats:
        _count_calls(h["body"], None, call_counts)

    inline_candidates = {name for name, sites in call_counts.items() if len(sites) == 1}
    # Also inline procedures with very short bodies regardless of call count
    for p in procedures:
        if len(p["body"]) <= 2 and p["name"] not in inline_candidates:
            inline_candidates.add(p["name"])

    if not inline_candidates:
        return procedures, hats

    # Build inline map
    proc_map = {p["name"]: p for p in procedures}

    def _do_inline(body):
        out = []
        for s in body:
            s = dict(s)
            if s["op"] == "procedures_call":
                name = s.get("fields", {}).get("PROCCODE", s.get("args", {}).get("PROCCODE", ""))
                call_args = s.get("args", {}).get("_args", [])
                if name in inline_candidates and name in proc_map:
                    proc = proc_map[name]
                    arg_names = proc.get("args", [])
                    subst = {}
                    for an, av in zip(arg_names, call_args):
                        subst[an] = av
                    # Unique id per call site so nested/repeated inlines never
                    # reuse the same temp name (temps are function-scoped locals).
                    _inline_uid[0] += 1
                    uid = _inline_uid[0]
                    # Insert set variable stmts before inlined body for call-by-value
                    pre_stmts = []
                    for an, av in zip(arg_names, call_args):
                        pre_stmts.append({
                            "op": "data_setvariableto",
                            "args": {"VALUE": av},
                            "fields": {"VARIABLE": _inline_temp_name(uid, an)},
                        })
                    inlined = []
                    def _subst_stmt(ss):
                        ss = dict(ss)
                        ss["args"] = {k: _subst_args(v) for k, v in ss.get("args", {}).items()}
                        if ss.get("sub"):
                            ss["sub"] = [_subst_stmt(s) for s in ss["sub"]]
                        if ss.get("sub2"):
                            ss["sub2"] = [_subst_stmt(s) for s in ss["sub2"]]
                        return ss

                    def _subst_args(node):
                        if isinstance(node, list):
                            return [_subst_args(v) for v in node]
                        if isinstance(node, dict):
                            node = dict(node)
                            if node.get("kind") == "arg" and node.get("name", "") in subst:
                                # Call-by-value: substitute with a temp variable that was set
                                # at the call site (not the raw expression, which avoids
                                # re-evaluation side effects from call-by-name semantics)
                                return {"kind": "var", "name": _inline_temp_name(uid, node['name'])}
                            if "args" in node:
                                if isinstance(node["args"], dict):
                                    node["args"] = {k: _subst_args(v) for k, v in node["args"].items()}
                                elif isinstance(node["args"], list):
                                    node["args"] = [_subst_args(v) for v in node["args"]]
                            return node
                        return node

                    inlined = [_subst_stmt(ps) for ps in proc["body"]]
                    out.extend(pre_stmts)
                    out.extend(inlined)
                    continue
            if s.get("sub"):
                s["sub"] = _do_inline(s["sub"])
            if s.get("sub2"):
                s["sub2"] = _do_inline(s["sub2"])
            out.append(s)
        return out

    for p in procedures:
        p["body"] = _do_inline(p["body"])
    for h in hats:
        h["body"] = _do_inline(h["body"])

    return procedures, hats


def _count_calls(body, owning_proc, call_counts):
    for s in body:
        if s["op"] == "procedures_call":
            name = s.get("fields", {}).get("PROCCODE", s.get("args", {}).get("PROCCODE", ""))
            if name != owning_proc:
                call_counts[name].append(s)
        if s.get("sub"):
            _count_calls(s["sub"], owning_proc, call_counts)
        if s.get("sub2"):
            _count_calls(s["sub2"], owning_proc, call_counts)


# ---- 21. Dead procedure elimination ------------------------------------

def _elim_dead_procedures(procedures, hats):
    """Remove procedure definitions never called from any hat or live procedure."""
    live = set()
    def _find_live_calls(body):
        for s in body:
            if s["op"] == "procedures_call":
                live.add(s.get("fields", {}).get("PROCCODE", s.get("args", {}).get("PROCCODE", "")))
            if s.get("sub"):
                _find_live_calls(s["sub"])
            if s.get("sub2"):
                _find_live_calls(s["sub2"])
    for h in hats:
        _find_live_calls(h["body"])
    # Iteratively find live procedures (called by other live procedures)
    changed = True
    while changed:
        changed = False
        for p in procedures:
            if p["name"] in live:
                old = len(live)
                _find_live_calls(p["body"])
                if len(live) > old:
                    changed = True
    keep = [p for p in procedures if p["name"] in live]
    return keep, hats


# ---- 24. Unused variable/list elimination --------------------------------

# Built-in sensing_of properties that are NOT custom variables (cross-sprite reads)
_SENSING_BUILTINS = frozenset({
    "x position", "y position", "direction", "size", "volume",
    "costume number", "costume name", "costume_number", "costume_name",
    "backdrop number", "backdrop name", "backdrop_number", "backdrop_name",
})
_SENSING_BUILTINS_LOWER = frozenset(p.lower() for p in _SENSING_BUILTINS)


def _collect_external_sensing_reads(all_targets):
    """Scan all targets' hats/procedures for sensing_of blocks that read
    variables from OTHER targets (or the stage). Returns a dict mapping
    target_name -> set of variable names that are read externally.
    This prevents _elim_unused_vars from pruning variables that are only
    referenced via cross-sprite [variable of Sprite] sensing blocks."""
    external = defaultdict(set)
    def _scan_expr(node, owning_target_name):
        if isinstance(node, dict):
            if node.get("kind") == "expr" and node.get("op") == "sensing_of":
                prop = (node.get("fields", {}).get("PROPERTY") or "").strip()
                # Skip built-in properties - they aren't custom variables
                if prop and prop.lower() not in _SENSING_BUILTINS_LOWER:
                    obj = node.get("args", {}).get("OBJECT", {})
                    # OBJECT is usually a const like "Sprite1" or "_stage_"
                    if isinstance(obj, dict) and obj.get("kind") == "const":
                        target_name = obj.get("v")
                        if isinstance(target_name, str) and target_name:
                            external[target_name].add(prop)
                    # Also handle the case where OBJECT might be a variable
                    elif isinstance(obj, dict) and obj.get("kind") == "var":
                        # Can't statically determine target - skip
                        pass
                    elif isinstance(obj, dict):
                        # Dynamic target name - conservatively mark all targets
                        if prop:
                            for t in all_targets:
                                external[t.get("name", "")].add(prop)
            for v in node.get("args", {}).values() if isinstance(node.get("args"), dict) else ():
                _scan_expr(v, owning_target_name)
            if isinstance(node.get("args"), list):
                for v in node["args"]:
                    _scan_expr(v, owning_target_name)
        elif isinstance(node, list):
            for v in node:
                _scan_expr(v, owning_target_name)
    def _scan_body(body, target_name):
        for s in body:
            for v in s.get("args", {}).values():
                _scan_expr(v, target_name)
            if s.get("sub"):
                _scan_body(s["sub"], target_name)
            if s.get("sub2"):
                _scan_body(s["sub2"], target_name)
    for t in all_targets:
        tname = t.get("name", "")
        for p in t.get("procedures", []):
            _scan_body(p["body"], tname)
        for h in t.get("hats", []):
            _scan_body(h["body"], tname)
    return external


def _collect_var_reads(procedures, hats):
    """Return set of variable names that are read in any hat or procedure body."""
    reads = set()
    def _scan_expr(node):
        if isinstance(node, dict):
            if node.get("kind") == "var":
                reads.add(node.get("name", ""))
            a = node.get("args") or {}
            if isinstance(a, dict):
                for v in a.values():
                    _scan_expr(v)
            elif isinstance(a, list):
                for v in a:
                    _scan_expr(v)
        elif isinstance(node, list):
            for v in node:
                _scan_expr(v)
    def _scan_body(body):
        for s in body:
            # Check args for variable reads
            for v in s.get("args", {}).values():
                _scan_expr(v)
            # data_changevariableby reads + writes
            if s["op"] == "data_changevariableby":
                vname = s.get("fields", {}).get("VARIABLE", "")
                if vname:
                    reads.add(vname)
            if s.get("sub"):
                _scan_body(s["sub"])
            if s.get("sub2"):
                _scan_body(s["sub2"])
    for p in procedures:
        _scan_body(p["body"])
    for h in hats:
        _scan_body(h["body"])
    return reads


def _elim_unused_var_stmts(body, keep_vars, cse_prefix=None):
    """Remove data_setvariableto / data_changevariableby targeting vars not in keep_vars."""
    out = []
    for s in body:
        op = s.get("op", "")
        if op in ("data_setvariableto", "data_changevariableby"):
            vname = s.get("fields", {}).get("VARIABLE", "")
            if vname and not vname.startswith("_inline_") and vname not in keep_vars:
                continue
        s = dict(s)
        if s.get("sub"):
            s["sub"] = _elim_unused_var_stmts(s["sub"], keep_vars, cse_prefix)
        if s.get("sub2"):
            s["sub2"] = _elim_unused_var_stmts(s["sub2"], keep_vars, cse_prefix)
        out.append(s)
    return out


def _elim_unused_vars(variables, procedures, hats, external_reads=None):
    """Remove variables that are never read in any hat or procedure body.
    If external_reads is provided (set of var names read by other targets
    via sensing_of), those are preserved too."""
    if not variables:
        return variables
    read_vars = _collect_var_reads(procedures, hats)
    if external_reads:
        read_vars |= external_reads
    return {k: v for k, v in variables.items() if k in read_vars}


# ---- 27. CFG simplification (flatten nested single-child blocks) --------

def _cfg_simplify(body):
    """Flatten trivial nested blocks: if { stmts } where condition is True, etc."""
    out = []
    for s in body:
        s = dict(s)
        if s.get("sub"):
            s["sub"] = _cfg_simplify(s["sub"])
        if s.get("sub2"):
            s["sub2"] = _cfg_simplify(s["sub2"])

        op = s["op"]
        # Flatten control_if with no else and single child that is also an if
        if op == "control_if" and not s.get("sub2", []):
            sub = s.get("sub", [])
            if len(sub) == 1 and sub[0]["op"] == "control_if":
                inner = sub[0]
                # Merge conditions with AND
                inner_cond = inner.get("args", {}).get("CONDITION")
                outer_cond = s.get("args", {}).get("CONDITION")
                if inner_cond is not None and outer_cond is not None:
                    merged = {"kind": "expr", "op": "operator_and",
                              "args": {"0": outer_cond, "1": inner_cond}, "fields": {}}
                    s["args"]["CONDITION"] = merged
                    s["sub"] = inner.get("sub", [])
        out.append(s)
    return out


# ---- 28. Nested repeat collapse ------------------------------------------

def _collapse_nested_repeats(body):
    """repeat a { repeat b { body } } → repeat a*b { body } when inner body is pure."""
    out = []
    for s in body:
        s = dict(s)
        if s.get("sub"):
            s["sub"] = _collapse_nested_repeats(s["sub"])
        if s.get("sub2"):
            s["sub2"] = _collapse_nested_repeats(s["sub2"])

        if s["op"] == "control_repeat":
            sub = s.get("sub", [])
            if len(sub) == 1 and sub[0]["op"] == "control_repeat":
                outer_times = s.get("args", {}).get("TIMES", {})
                inner = sub[0]
                inner_times = inner.get("args", {}).get("TIMES", {})
                if outer_times.get("kind") == "const" and inner_times.get("kind") == "const":
                    ov = outer_times.get("v", 0)
                    iv = inner_times.get("v", 0)
                    if isinstance(ov, (int, float)) and isinstance(iv, (int, float)):
                        combined = int(ov) * int(iv)
                        s["args"]["TIMES"] = {"kind": "const", "v": float(combined)}
                        s["sub"] = inner.get("sub", [])
        out.append(s)
    return out


# ---- Master simplification pass (statement level) -----------------------

def _ir_simplify_body(body):
    """Apply all statement-level simplifications."""
    out = []
    for s in body:
        s = dict(s)
        op = s["op"]
        args = s.get("args", {})

        s["args"] = {k: _ir_simplify_expr(v) for k, v in args.items()}

        if s.get("sub"):
            s["sub"] = _ir_simplify_body(s["sub"])
        if s.get("sub2"):
            s["sub2"] = _ir_simplify_body(s["sub2"])

        # 10. No-op statement removal
        if _is_noop_stmt(s):
            continue

        # 11/18/19. Condition folding + empty-block
        if op in ("control_if", "control_if_else"):
            cond = s["args"].get("CONDITION", {})
            if cond is None or cond == {}:
                # Malformed/empty condition would emit `if '':` (a dead guard,
                # e.g. wrapping //-proc calls). Treat as constant-false.
                cond = {"kind": "const", "v": 0.0}
            if isinstance(cond, dict) and cond.get("kind") == "const":
                cv = cond.get("v", 0.0)
                truthy = bool(cv)
                if op == "control_if":
                    body_blk = s.get("sub", [])
                    if truthy:
                        out.extend(body_blk)
                else:
                    if truthy:
                        out.extend(s.get("sub", []))
                    else:
                        out.extend(s.get("sub2", []))
                continue
            # Empty-body elimination
            if op == "control_if":
                if not s.get("sub", []):
                    continue
            # if-else where one branch is empty
            if op == "control_if_else":
                sub = s.get("sub", [])
                sub2 = s.get("sub2", [])
                if not sub and not sub2:
                    continue
                if not sub:
                    # if-cond else body → if not cond then body
                    s["op"] = "control_if"
                    cond = s["args"].get("CONDITION", {"kind": "const", "v": 1.0})
                    s["args"]["CONDITION"] = {
                        "kind": "expr", "op": "operator_not",
                        "args": {"0": cond}, "fields": {}
                    }
                    s["sub"] = sub2
                    if "sub2" in s:
                        del s["sub2"]
                elif not sub2:
                    s["op"] = "control_if"
                    if "sub2" in s:
                        del s["sub2"]

        # 11. Empty-loop elimination (repeat/forever with empty body)
        # Only delete truly useless loops: repeat/forever with empty body.
        # repeat_until and while with empty body are polling/wait patterns
        # (e.g. "repeat until touching color" with no body = wait-for-boundary).
        if op in ("control_repeat", "control_forever"):
            if not s.get("sub", []):
                continue

        # 11. for_each over empty range
        if op == "control_for_each" and not s.get("sub", []):
            continue

        # 18. repeat_until false → forever
        if op == "control_repeat_until":
            cond = s["args"].get("CONDITION", {})
            if isinstance(cond, dict) and cond.get("kind") == "const":
                if cond.get("v", 0.0):
                    continue  # repeat until True = never executes
                s["op"] = "control_forever"
                s["args"] = {}

        # 18. while true → forever
        if op == "control_while":
            cond = s["args"].get("CONDITION", {})
            if isinstance(cond, dict) and cond.get("kind") == "const":
                if cond.get("v", 0.0):
                    s["op"] = "control_forever"
                    s["args"] = {}
                else:
                    continue

        # 19. Single-iteration repeat → inline
        if op == "control_repeat":
            times = s["args"].get("TIMES", {})
            if isinstance(times, dict) and times.get("kind") == "const":
                tv = times.get("v", 0)
                if isinstance(tv, (int, float)):
                    itv = int(tv)
                else:
                    try:
                        itv = int(tv)
                    except (ValueError, TypeError):
                        itv = 0
                if itv <= 0:
                    continue
                if itv == 1:
                    out.extend(s.get("sub", []))
                    continue

        # 22. Tail-call collapse: consecutive identical calls
        if op == "procedures_call" and out:
            last = out[-1]
            if last["op"] == "procedures_call":
                lname = last.get("fields", {}).get("PROCCODE", "")
                sname = s.get("fields", {}).get("PROCCODE", "")
                if lname == sname and str(last.get("args", {}).get("_args", [])) == str(s.get("args", {}).get("_args", [])):
                    continue

        # PATCH 7: broadcast + stop-all → broadcast_and_wait (prevents
        # broadcast being lost when stop-all kills receivers)
        if op == "control_stop" and out:
            _stop_mode = s.get("fields", {}).get("STOP_OPTION", "'all'")
            if _stop_mode in ("'all'", "'other scripts in sprite'"):
                _prev = out[-1]
                if _prev.get("op") == "event_broadcast":
                    _prev["op"] = "event_broadcastandwait"
                    continue

        out.append(s)
    return out


# ---- 12. Dead-code elimination after terminators -------------------------

def _ir_dead_stmt_elim(body):
    """Remove statements after terminators (control_stop).
    All control_stop modes return from the generator, so statements
    after them are unreachable."""
    out = []
    for s in body:
        out.append(s)
        if s.get("op") == "control_stop":
            break
    return out


# ---- PATCH 14. Collapse list reset+add patterns ---------------------------

def _collapse_list_resets(body):
    """Collapse 'delete all of L' followed by N 'add X to L' into a single
    list-literal assignment: L = [X, Y, Z, ...]."""
    out = []
    i = 0
    while i < len(body):
        s = body[i]
        if (s.get("op") == "data_deletealloflist"
                and i + 1 < len(body)
                and body[i + 1].get("op") == "data_addtolist"):
            lst_name = s.get("fields", {}).get("LIST", "")
            items = []
            j = i + 1
            while j < len(body):
                sj = body[j]
                if (sj.get("op") == "data_addtolist"
                        and sj.get("fields", {}).get("LIST", "") == lst_name):
                    items.append(sj.get("args", {}).get("ITEM", {"kind": "const", "v": ""}))
                    j += 1
                else:
                    break
            if len(items) >= 2:
                out.append({
                    "op": "_list_direct_assign",
                    "fields": {"LIST": lst_name},
                    "args": {"ITEMS": items},
                })
                i = j
                continue
        out.append(s)
        i += 1
    return out


# ---- 22. Tail-call / redundant call elimination -------------------------

def _tail_call_elim(procedures):
    """Inline tail calls: if a procedure body is a single call to another
    (non-recursive) procedure, inline the target's body."""
    proc_map = {p["name"]: p for p in procedures}
    for p in procedures:
        body = p["body"]
        if len(body) == 1 and body[0]["op"] == "procedures_call":
            name = body[0].get("fields", {}).get("PROCCODE", "")
            if name in proc_map and name != p["name"]:
                target = proc_map[name]
                if target["body"]:  # don't inline empty bodies
                    # Map caller args to target parameter names before copying body
                    call_args = body[0].get("args", {}).get("_args", [])
                    target_args = target.get("args", [])
                    # Purity guard: skip inlining if any caller arg is dynamic.
                    # Without this, picking e.g. pick_random(1,10) as an arg and
                    # referencing that parameter twice in the inlined body would
                    # call-by-name re-evaluate the pick on every use.
                    def _arg_is_pure(a):
                        if not isinstance(a, dict):
                            return True
                        k = a.get("kind")
                        if k in ("const", "var", "arg", "list"):
                            return True
                        if k == "expr":
                            return a.get("op", "") in _PURE_OPS
                        return False  # "call" or anything else is not safe
                    if any(not _arg_is_pure(a) for a in call_args):
                        continue
                    # Build substitution: param_name -> caller_arg_value
                    subst = {}
                    for i, arg_name in enumerate(target_args):
                        if i < len(call_args) and call_args[i] is not None:
                            subst[arg_name] = call_args[i] 
                    # Copy and substitute arg refs in the target body
                    new_body = [dict(s) for s in target["body"]]
                    def _subst_expr(node):
                        if isinstance(node, dict):
                            if node.get("kind") == "arg" and node.get("name", "") in subst:
                                return subst[node["name"]]
                            a = node.get("args") or {}
                            if isinstance(a, dict):
                                node["args"] = {k: _subst_expr(v) for k, v in a.items()}
                            elif isinstance(a, list):
                                node["args"] = [_subst_expr(v) for v in a]
                        return node
                    def _subst_stmt(stmt):
                        stmt["args"] = {k: _subst_expr(v) for k, v in stmt.get("args", {}).items()}
                        if stmt.get("sub"):
                            for ss in stmt["sub"]:
                                _subst_stmt(ss)
                        if stmt.get("sub2"):
                            for ss in stmt["sub2"]:
                                _subst_stmt(ss)
                    for s in new_body:
                        _subst_stmt(s)
                    p["body"] = new_body
    return procedures


# ---- 23. Argument default flattening ------------------------------------

def _replace_in_expr(node, arg_name, const_val):
    """Replace all arg references in an expression with a constant."""
    if isinstance(node, dict):
        if node.get("kind") == "arg" and node.get("name") == arg_name:
            return dict(const_val)
        if "args" in node:
            if isinstance(node["args"], dict):
                node["args"] = {k: _replace_in_expr(v, arg_name, const_val)
                                 for k, v in node["args"].items()}
            elif isinstance(node["args"], list):
                node["args"] = [_replace_in_expr(v, arg_name, const_val)
                                for v in node["args"]]
    return node


def _replace_arg_refs(body, arg_name, const_val):
    """Replace all arg refs in a statement body with a constant."""
    for s in body:
        s["args"] = {k: _replace_in_expr(v, arg_name, const_val)
                      for k, v in s.get("args", {}).items()}
        if s.get("sub"):
            _replace_arg_refs(s["sub"], arg_name, const_val)
        if s.get("sub2"):
            _replace_arg_refs(s["sub2"], arg_name, const_val)


def _flatten_arg_defaults(procedures, hats):
    """If a procedure parameter always receives the same constant value
    at every call site, inline that constant and eliminate the parameter."""
    call_args = defaultdict(list)

    def _collect_calls(body):
        for s in body:
            if s["op"] == "procedures_call":
                name = s.get("fields", {}).get("PROCCODE", "")
                args = s.get("args", {}).get("_args", [])
                call_args[name].append(args)
            if s.get("sub"):
                _collect_calls(s["sub"])
            if s.get("sub2"):
                _collect_calls(s["sub2"])

    for h in hats:
        _collect_calls(h["body"])
    for p in procedures:
        _collect_calls(p["body"])

    for p in procedures:
        name = p["name"]
        arg_names = list(p.get("args", []))
        if not arg_names or name not in call_args:
            continue
        sites = call_args[name]
        if not sites:
            continue
        for idx, arg_name in enumerate(arg_names):
            vals = []
            all_const = True
            for site_args in sites:
                if idx >= len(site_args) or site_args[idx] is None:
                    all_const = False
                    break
                v = site_args[idx]
                if isinstance(v, dict) and v.get("kind") == "const":
                    vals.append(v)
                else:
                    all_const = False
                    break
            if all_const and vals and \
               len(set(str(v.get("v")) for v in vals)) == 1:
                # All call sites pass the same constant -> inline it
                # We replace arg refs in the body but KEEP the param
                # in the signature (call sites are not updated, so
                # removing the param would cause arg-count mismatch).
                const_val = vals[0]
                _replace_arg_refs(p["body"], arg_name, const_val)
    return procedures, hats


# ---- Main pipeline ------------------------------------------------------

def _ir_opt(procedures, hats, cse_prefix=None):
    """Run all IR optimisation passes in order."""
    # Pick a CSE temporary prefix that cannot collide with any real variable
    # name, so CSE locals are never mistaken for (or overwrite) project vars.
    if cse_prefix is None:
        cse_prefix = _cse_safe_prefix(_collect_var_reads(procedures or [], hats or []))
    # 20. Inline small/single-use procedures
    procedures, hats = _inline_procedures(procedures, hats)
    # 21. Remove dead procedures
    procedures, hats = _elim_dead_procedures(procedures, hats)
    # 22. Tail-call elimination
    procedures = _tail_call_elim(procedures)
    # 23. Argument default flattening
    procedures, hats = _flatten_arg_defaults(procedures, hats)

    for p in procedures:
        body = p["body"]
        # 8. Constant propagation
        body, _ = _ir_const_prop(body)
        # 2-6. Expression simplification (algebraic, boolean, strength, canonical)
        body = _ir_simplify_body(body)
        # 1. Constant folding
        body = _fold_body(body)
        # 28. Nested repeat collapse
        body = _collapse_nested_repeats(body)
        # 16. Loop-invariant code motion
        body = _licm_body(body, cse_prefix=cse_prefix)
        # 7. Common subexpression elimination
        body, _ = _ir_cse_block(body, cse_prefix)
        # 17. Loop strength reduction (unroll small loops)
        body = _ir_loop_strength(body)
        # 13/14. Collapse duplicates
        body = _collapse_duplicates(body)
        # PATCH 14. Collapse list reset+add patterns
        body = _collapse_list_resets(body)
        # 27. CFG simplification
        body = _cfg_simplify(body)
        # 1a. Re-fold after propagation: turns provably-constant inline/cse
        # conditions such as `if _gt(_inline_dir, 0)` into const nodes.
        body = _fold_body(body)
        # 11/18/19. Re-run simplification AFTER folding so branch elimination
        # (control_if/if_else with a const condition) can drop dead branches.
        body = _ir_simplify_body(body)
        # 12. Dead stmt elimination
        body = _ir_dead_stmt_elim(body)
        p["body"] = body

    for h in hats:
        body = h["body"]
        body, _ = _ir_const_prop(body)
        body = _ir_simplify_body(body)
        body = _fold_body(body)
        body = _collapse_nested_repeats(body)
        body = _licm_body(body, cse_prefix=cse_prefix)
        body, _ = _ir_cse_block(body, cse_prefix)
        body = _ir_loop_strength(body)
        body = _collapse_duplicates(body)
        body = _collapse_list_resets(body)
        body = _cfg_simplify(body)
        body = _fold_body(body)
        body = _ir_simplify_body(body)
        body = _ir_dead_stmt_elim(body)
        h["body"] = body

    # 15. Unreachable script removal: drop hats whose bodies were
    # eliminated entirely (e.g. scripts starting with control_stop).
    hats = [h for h in hats if h.get("body")]

    return procedures, hats, cse_prefix


def _asset_filename(meta, used):
    """Build a real-name filename for a costume/sound, keeping the original
    extension. Falls back to the raw md5ext if no name is present, and
    suffixes a short hash on collision (defensive: per-sprite folders make
    collisions impossible for distinct Scratch names, but we guard anyway)."""
    raw = meta.get("md5ext", "")
    ext = "." + raw.rsplit(".", 1)[1] if "." in raw else ""
    base = meta.get("name") or raw
    clean = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(base)).strip().strip("._")
    if not clean:
        clean = raw or "asset"
    cand = clean + ext
    if cand in used:
        short = raw[:8] if raw else "x"
        cand = f"{clean}_{short}{ext}"
        n = 2
        while cand in used:
            cand = f"{clean}_{short}_{n}{ext}"
            n += 1
    used.add(cand)
    return cand


def extract_sb3(sb3_path, extract_dir):
    """Open a .sb3 (a zip) and return its project.json contents as a dict.

    The .sb3 is treated purely as an in-memory/intermediate archive. We read
    project.json directly out of the zip, stream asset bytes straight into
    data/<target>/<realname>.<ext> (named after the costume/sound in the
    project, not the opaque md5), and rewrite each costume/sound's ``md5ext``
    field in the returned dict to that real filename. Nothing from the archive
    is extracted to a top-level location and project.json is never written to
    disk — all info main.py needs is embedded into the generated code.
    """
    import zipfile
    sb3_path = Path(sb3_path)
    extract_dir = Path(extract_dir)
    if not sb3_path.exists():
        raise FileNotFoundError(f".sb3 not found: {sb3_path}")
    try:
        with zipfile.ZipFile(sb3_path, "r") as z:
            if "project.json" not in z.namelist():
                raise FileNotFoundError(f"project.json missing inside .sb3: {sb3_path}")
            # read project.json out of the archive into memory
            try:
                with z.open("project.json") as fp:
                    data = json.load(fp)
            except json.JSONDecodeError as e:
                raise ValueError(f"project.json inside {sb3_path} is not valid JSON: {e}") from e
            # stream asset bytes straight into data/<target>/<realname>.<ext>
            _organize_assets_from_zip(z, data, extract_dir)
    except zipfile.BadZipFile as e:
        raise ValueError(f"corrupt or incomplete .sb3 archive: {sb3_path} ({e})") from e
    log.info("read project.json + assets from .sb3 (no top-level extraction)")
    return data


def _organize_assets_from_zip(z, data, out_dir):
    """Stream costume/sound bytes from an open sb3 zip into
    data/<target_name>/<realname>.<ext> subdirs, rewriting each asset's
    md5ext in *data* to the real filename so generated code resolves it."""
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    names = z.namelist()
    for t in data.get("targets", []):
        tname = t.get("name", "unknown")
        tdir = data_dir / tname
        tdir.mkdir(parents=True, exist_ok=True)
        used = set()
        for key in ("costumes", "sounds"):
            for c in t.get(key, []):
                raw = c.get("md5ext", "")
                if not raw or raw not in names:
                    continue
                fname = _asset_filename(c, used)
                # defensive: never clobber an existing real-named file
                dst = tdir / fname
                if not dst.exists():
                    try:
                        with z.open(raw) as src, open(dst, "wb") as out:
                            out.write(src.read())
                    except Exception as e:
                        log.warning("skipping unreadable asset %s in %s: %s", raw, tname, e)
                        continue
                # point the metadata at the real filename
                c["md5ext"] = fname
    log.info("organized assets into data/<target>/ (named after project)")


def _parse_var_value(v):
    if isinstance(v, str):
        try:
            return int(v) if v.lstrip('-').isdigit() else float(v)
        except (ValueError, TypeError):
            pass
    return v


def parse_project(project_path):
    """Parse a Scratch project.json file (or an already-loaded dict) and
    return the IR dict. Accepts either a path to project.json or a dict that
    was read out of an .sb3 archive directly.
    """
    if isinstance(project_path, dict):
        data = project_path
    else:
        project_path = Path(project_path)
        if not project_path.exists():
            raise FileNotFoundError(f"project.json not found: {project_path}")
        with open(project_path, encoding="utf-8") as f:
            data = json.load(f)

    broadcasts = {}
    for t in data.get("targets", []):
        broadcasts.update({bid: bn for bid, bn in t.get("broadcasts", {}).items()})

    # ---- Phase 1: parse + optimize every target (defer variable elimination) --
    targets = []
    for t in data.get("targets", []):
        parser = IRParser(t, broadcasts)
        procedures, hats, scripts = parser.build()
        procedures, hats, cse_prefix = _ir_opt(procedures, hats)
        variables = {v[0]: _parse_var_value(v[1]) for v in t.get("variables", {}).values()}
        # NOTE: _elim_unused_vars / _elim_unused_var_stmts are deferred until
        # after we've scanned ALL targets for cross-sprite sensing_of reads
        # (see Phase 2 and Phase 3 below).  Running them here would prune
        # assignments to variables that are only read by OTHER sprites.
        lists = {li[0]: list(li[1]) for li in t.get("lists", {}).values()}
        costumes = [{
            "name": c.get("name"),
            "md5ext": c.get("md5ext"),
            "bitmapResolution": c.get("bitmapResolution", 1),
            "rotationCenterX": c.get("rotationCenterX", 0),
            "rotationCenterY": c.get("rotationCenterY", 0),
        } for c in t.get("costumes", [])]
        sounds = [{
            "name": s.get("name"),
            "md5ext": s.get("md5ext"),
            "soundId": s.get("soundId"),
            "rate": s.get("rate"),
            "sampleCount": s.get("sampleCount"),
            "dataFormat": s.get("dataFormat"),
        } for s in t.get("sounds", [])]
        targets.append({
            "name": t.get("name"),
            "isStage": t.get("isStage", False),
            "variables": variables,
            "lists": lists,
            "costumes": costumes,
            "sounds": sounds,
            "procedures": procedures,
            "hats": hats,
            "cse_prefix": cse_prefix,
            "scripts": scripts,
        })

    # ---- Phase 2: scan ALL targets for cross-sprite sensing_of reads -----
    # Before eliminating variables we must know which variables are read
    # externally by OTHER targets (e.g. [my-var v] of [Sprite1 v]).
    external_reads = _collect_external_sensing_reads(targets)

    # ---- Phase 3: now eliminate unused variables per target ---------------
    # With full knowledge of cross-sprite reads, we can safely prune both
    # variable definitions AND assignment statements that are truly dead.
    # Variables read by another target's sensing_of block are preserved
    # along with all their assignments.
    # Stage-global variable names: a sprite may read/write these even though
    # they don't appear in the sprite's own variable table, so statement
    # elimination for sprites must treat them as live.
    stage_var_names = set()
    for tgt in targets:
        if tgt["isStage"]:
            stage_var_names |= set(tgt["variables"].keys())
    for tgt in targets:
        all_read = _collect_var_reads(tgt.get("procedures", []), tgt.get("hats", []))
        if tgt["isStage"]:
            keep_vars = set(tgt["variables"].keys()) | all_read
            for p in tgt["procedures"]:
                p["body"] = _elim_unused_var_stmts(p["body"], keep_vars, tgt["cse_prefix"])
            for h in tgt["hats"]:
                h["body"] = _elim_unused_var_stmts(h["body"], keep_vars, tgt["cse_prefix"])
            continue
        ext_vars = external_reads.get(tgt["name"], set())
        tgt["variables"] = _elim_unused_vars(
            tgt["variables"], tgt["procedures"], tgt["hats"], ext_vars,
        )
        keep_vars = set(tgt["variables"].keys()) | stage_var_names | all_read
        for p in tgt["procedures"]:
            p["body"] = _elim_unused_var_stmts(p["body"], keep_vars, tgt["cse_prefix"])
        for h in tgt["hats"]:
            h["body"] = _elim_unused_var_stmts(h["body"], keep_vars, tgt["cse_prefix"])

    return {"meta": data.get("meta", {}), "broadcasts": broadcasts, "targets": targets}


# ===================================================================
#  TEXT EXTRACTOR  (human-readable pseudo-code)
# ===================================================================

class TextExtractor:
    """Render a Scratch target as human-readable pseudo-code."""

    def __init__(self, target):
        self.target = target
        self.blocks = {k: v for k, v in target["blocks"].items() if isinstance(v, dict)}

    def render_input(self, inp):
        if inp is None:
            return ""
        if not isinstance(inp, list):
            return str(inp)
        kind = inp[0]
        if kind == 1:
            return self._literal_str(inp[1])
        if kind == 2:
            return self.render_block(inp[1])
        if kind == 3:
            ref = inp[1]
            if isinstance(ref, str) and ref in self.blocks:
                return self.render_block(ref)
            shadow = inp[2] if len(inp) > 2 else None
            return self._literal_str(shadow)
        return str(inp)

    def _literal_str(self, val):
        if isinstance(val, list):
            t = val[0]
            if t in (4, 5, 7, 8, 9, 10, 11, 12):
                return repr(val[1])
            if t == 6:
                return repr(val[1])
            return repr(val)
        return repr(val) if isinstance(val, str) else str(val)

    def render_block(self, ref):
        if not isinstance(ref, str) or ref not in self.blocks:
            return f"<{ref}>"
        b = self.blocks[ref]
        op = b.get("opcode", "")
        inp = b.get("inputs", {})
        fld = b.get("fields", {})

        if op == "data_variable":
            return field_name(b, "VARIABLE")
        if op == "data_listcontents":
            return "{" + field_name(b, "LIST") + "}"
        if op in ("argument_reporter_string_number", "argument_reporter_boolean"):
            return field_name(b, "VALUE")
        if op == "procedures_call":
            return self._render_call(b)
        if op == "procedures_prototype":
            return self._proccode(b)
        if op in REPORTER_SIMPLE:
            fmt = REPORTER_SIMPLE[op]
            slots = list(inp.values())
            args = [self.render_input(s) for s in slots]
            out = fmt
            for fk in ("LIST", "VARIABLE"):
                if fk in fld:
                    out = out.replace("{" + fk + "}", field_name(b, fk))
            try:
                return out % tuple(args)
            except Exception:
                return out + " " + str(args)
        if op in ("sensing_keypressed",):
            return f"(key {self.render_input(inp.get('KEY'))} pressed?)"
        if op == "sensing_of":
            return f"({field_name(b, 'PROPERTY')} of {self.render_input(inp.get('OBJECT'))})"
        if op in ("operator_compare", "operator_equals"):
            return f"({self.render_input(inp.get('OPERAND1'))} = {self.render_input(inp.get('OPERAND2'))})"
        return f"<{op} { {k: self.render_input(v) for k, v in inp.items()} }>"

    def _proccode(self, b):
        m = b.get("mutation", {})
        proccode = m.get("proccode", "")
        names = json.loads(m.get("argumentnames", "[]"))
        ids = json.loads(m.get("argumentids", "[]"))
        inputs = b.get("inputs", {})
        args = []
        for i, aid in enumerate(ids):
            if aid in inputs:
                args.append(self.render_input(inputs[aid]))
            else:
                args.append("?" + (names[i] if i < len(names) else "?"))
        parts = proccode.split("%s")
        res = parts[0]
        for i, p in enumerate(parts[1:]):
            a = args[i] if i < len(args) else "?"
            res += a + p
        return res

    def _render_call(self, b):
        m = b.get("mutation", {})
        proccode = m.get("proccode", "")
        argids = json.loads(m.get("argumentids", "[]"))
        argnames = json.loads(m.get("argumentnames", "[]"))
        inputs = b.get("inputs", {})
        name = proccode
        for n in argnames:
            name = name.replace("%s", n, 1)
        args = [self.render_input(inputs[aid]) if aid in inputs else ""
                for aid in argids]
        if args and any(a.strip() for a in args):
            return f"{name} ({', '.join(args)})"
        return name

    def render_stmt(self, ref, indent):
        lines = []
        cur = ref
        while cur:
            if not isinstance(cur, str) or cur not in self.blocks:
                break
            b = self.blocks[cur]
            op = b.get("opcode", "")
            inp = b.get("inputs", {})
            pad = "  " * indent

            if op in SUBSTACKS:
                keys = SUBSTACKS[op]
                head_inp = (self.render_input(inp.get(keys[0]))
                            if keys[0] in ("CONDITION", "TIMES", "VALUE", "VARIABLE") else "")
                head = self._stmt_head(op, b, head_inp)
                lines.append(pad + head)
                sub = inp.get("SUBSTACK")
                if sub:
                    lines += self.render_stmt(sub[1], indent + 1)
                if "SUBSTACK2" in keys:
                    lines.append(pad + "else")
                    sub2 = inp.get("SUBSTACK2")
                    if sub2:
                        lines += self.render_stmt(sub2[1], indent + 1)
            elif op == "procedures_definition":
                proto = inp.get("custom_block", [None, None])[1]
                pname = self._proccode(self.blocks[proto]) if isinstance(proto, str) and proto in self.blocks else "procedure"
                warp = ""
                if isinstance(proto, str) and proto in self.blocks:
                    if self.blocks[proto].get("mutation", {}).get("warp") == "true":
                        warp = "  [warp]"
                lines.append(pad + "define " + pname + warp)
                nxt = b.get("next")
                if nxt:
                    lines += self.render_stmt(nxt, indent + 1)
                lines.append("")
            elif op in EVENT_HATS:
                tag = EVENT_HATS[op]
                if op == "event_whenbroadcastreceived":
                    tag = "when I receive " + field_name(b, "BROADCAST_OPTION")
                elif op == "event_whenkeypressed":
                    tag = "when " + field_name(b, "KEY_OPTION") + " key pressed"
                elif op == "event_whenbackdropswitchesto":
                    tag = "when backdrop switches to " + field_name(b, "BACKDROP")
                lines.append(pad + tag)
            elif op in STMT_SIMPLE:
                fmt = STMT_SIMPLE[op]
                slots = list(inp.values())
                args = [self.render_input(s) for s in slots]
                txt = fmt
                for fk in ("VARIABLE", "LIST"):
                    if fk in b.get("fields", {}):
                        txt = txt.replace("{" + fk + "}", field_name(b, fk))
                try:
                    txt = txt % tuple(args)
                except Exception:
                    pass
                lines.append(pad + txt)
            elif op == "procedures_call":
                lines.append(pad + self._render_call(b))
            else:
                args_str = {k: self.render_input(v) for k, v in inp.items()}
                lines.append(pad + f"<{op} {args_str}>")

            cur = b.get("next")
        return lines

    def _stmt_head(self, op, b, cond):
        inp = b.get("inputs", {})
        if op == "control_if":
            return f"if {cond} then"
        if op == "control_if_else":
            return f"if {cond} then"
        if op == "control_repeat":
            return f"repeat {cond}"
        if op == "control_repeat_until":
            return f"repeat until {cond}"
        if op == "control_while":
            return f"while {cond}"
        if op == "control_for_each":
            return f"for each {field_name(b, 'VARIABLE')} in {self.render_input(inp.get('VALUE'))}"
        if op == "control_forever":
            return "forever"
        return f"<{op}>"

    def extract(self):
        roots = [k for k, b in self.blocks.items() if b.get("parent") is None]
        defs = [k for k in roots if self.blocks[k].get("opcode") == "procedures_definition"]
        hats = [k for k in roots if self.blocks[k].get("opcode") != "procedures_definition"]
        scripts = []
        for k in defs:
            scripts.append(self.render_stmt(k, 0))
        for k in hats:
            scripts.append(self.render_stmt(k, 0))
        return scripts


def extract_text(project_path, output_dir):
    """Generate human-readable text files for each sprite."""
    project_path = Path(project_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(project_path, encoding="utf-8") as f:
        data = json.load(f)

    index = []
    for ti, t in enumerate(data.get("targets", [])):
        name = t.get("name", f"target_{ti}")
        fname = f"{ti:02d}_{sanitize(name)}.txt"

        vars_block = [f"  {v[0]} = {v[1]!r}"
                      for v in t.get("variables", {}).values()]
        lists_block = []
        for lid, li in t.get("lists", {}).items():
            val = li[1]
            if isinstance(val, list) and len(val) > 20:
                lists_block.append(f"  {li[0]} = <list len={len(val)}, shown below>")
            else:
                lists_block.append(f"  {li[0]} = {val!r}")

        ex = TextExtractor(t)
        scripts = ex.extract()

        lines = [f"# Target: {name}  (isStage={t.get('isStage')})",
                 f"# blocks: {len(ex.blocks)}\n"]
        if vars_block:
            lines.append("VARIABLES:\n" + "\n".join(vars_block) + "\n")
        if lists_block:
            lines.append("LISTS:\n" + "\n".join(lists_block) + "\n")
        lines.append("SCRIPTS:\n")
        for s in scripts:
            lines.append("\n".join(s) + "\n")

        with open(output_dir / fname, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        index.append((name, fname, len(ex.blocks)))
        log.info("extracted %s", fname)

    with open(output_dir / "INDEX.md", "w", encoding="utf-8") as f:
        f.write("# Extracted Scratch project\n\n")
        f.write(f"Source: {project_path}\n\n")
        f.write("| # | Target | Blocks | File |\n")
        f.write("|---|--------|--------|------|\n")
        for i, (n, fn, c) in enumerate(index):
            f.write(f"| {i} | {n} | {c} | {fn} |\n")

    log.info("text extraction done — %d targets", len(index))
    return output_dir


# ===================================================================
#  PYTHON EMITTER  (IR → real Python modules, no JSON blobs)
# ===================================================================

def emit_python(ir_data, output_dir, opts=None):
    """Generate one Python module per sprite from IR data.

    opts: dict with optional keys:
        target_fps (float, default 60)
        scale (int, default 2)
        stage_w (int, default 480)
        stage_h (int, default 360)
        debug (bool, default False)

    Each module contains real Python code — actual functions with
    if/while/for control flow — not JSON blobs for an interpreter.
    Also generates _engine.py (Sprite/Engine/operators), _display.py
    (tkinter rendering), and main.py (entry point).  Validates all
    generated files compile and reports any errors.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if opts is None:
        opts = {}
    opts.setdefault("target_fps", 60.0)
    opts.setdefault("scale", 2)
    opts.setdefault("stage_w", 480)
    opts.setdefault("stage_h", 360)
    opts.setdefault("debug", False)
    # lists larger than this (serialized JSON bytes) are written to
    # data/<target>/<list>.json and loaded lazily instead of inlined
    opts.setdefault("asset_list_min_bytes", 4096)
    # targets whose emitted module exceeds this many source lines are
    # written as a package (NN_name/__init__.py) rather than a flat file
    opts.setdefault("package_max_lines", 600)

    # ---- generate shared modules -------------------------------------------
    _generate_engine(output_dir, opts)
    _generate_display(output_dir, opts)
    _generate_debug_panel(output_dir, opts)

    # ---- generate one module per sprite ------------------------------------
    generated = []
    data_dir = output_dir / "data"
    for ti, t in enumerate(ir_data["targets"]):
        name = t["name"]
        mod_base = f"{ti:02d}_{pyname(name)}"
        # 1. detect "big" lists and externalise them to data/<target>/<list>.json
        #    using the SAME target dir name as _organize_assets (raw name, not
        #    the python-safe one) so list assets sit beside the costumes/sounds.
        big_lists = {}
        tdir = data_dir / name
        for lname, lval in t.get("lists", {}).items():
            try:
                blob = json.dumps(lval, ensure_ascii=False)
            except Exception:
                blob = ""
            if len(blob.encode("utf-8")) >= opts["asset_list_min_bytes"]:
                tdir.mkdir(parents=True, exist_ok=True)
                (tdir / f"{pyname(lname)}.json").write_text(blob, encoding="utf-8")
                big_lists[lname] = f"data/{name}/{pyname(lname)}.json"
                log.info("externalised list %r of %r (%d bytes)",
                         lname, name, len(blob.encode("utf-8")))
        # Canonical set of global (stage-owned) variable names, so the emitter
        # avoids the fast sp.vars[...] path for globals a sprite may also carry.
        stage_globals = set()
        for _st in ir_data["targets"]:
            if _st.get("isStage"):
                stage_globals |= set(_st.get("variables", {}).keys())
        emitter = _PyEmitter(t, ti, global_vars=stage_globals)
        emitter.big_lists = big_lists
        code = emitter.emit()
        # 2. large modules become a properly factored package:
        #    __init__.py (sprite + register), procs.py, hats.py
        if isinstance(code, dict):
            pkg = output_dir / mod_base
            pkg.mkdir(parents=True, exist_ok=True)
            for fn, c in code.items():
                (pkg / fn).write_text(c, encoding="utf-8")
            fname = mod_base + "/__init__.py"
            log.info("emitted package %s/ (%d files)", mod_base, len(code))
        elif code.count("\n") + 1 > opts["package_max_lines"]:
            emitter.package = True
            code = emitter.emit()  # now a dict of submodules
            pkg = output_dir / mod_base
            pkg.mkdir(parents=True, exist_ok=True)
            for fn, c in code.items():
                (pkg / fn).write_text(c, encoding="utf-8")
            fname = mod_base + "/__init__.py"
            log.info("emitted package %s/ (%d files)", mod_base, len(code))
        else:
            (output_dir / (mod_base + ".py")).write_text(code, encoding="utf-8")
            fname = mod_base + ".py"
            log.info("emitted %s", fname)
        generated.append((name, fname))

    # ---- generate main.py --------------------------------------------------
    _generate_main(ir_data, output_dir, generated, opts)

    manifest_lines = [
        "# Auto-generated manifest",
        "MODULES = [",
    ]
    for name, fname in generated:
        mod = fname[:-3]
        manifest_lines.append(f"    ({name!r}, {mod!r}),")
    manifest_lines.append("]")
    manifest_lines.append("")
    (output_dir / "__manifest__.py").write_text("\n".join(manifest_lines), encoding="utf-8")

    log.info("emission done — %d targets", len(generated))

    # ---- validate generated files ------------------------------------------
    errors = _validate_generated(output_dir, ir_data)
    if errors:
        log.error("VALIDATION FAILED — %d file(s) have errors:", len(errors))
        for fname, msg in errors:
            print(f"  {fname}: {msg}", file=sys.stderr)
        sys.exit(1)
    else:
        log.info("All generated files compile OK")

    # ---- generate README ------------------------------------------------
    _generate_readme(output_dir, ir_data, generated, opts)

    return output_dir


def _generate_readme(output_dir, ir_data, generated, opts):
    """Write a README.md into the decompiled directory with run info."""
    output_dir = Path(output_dir)
    from datetime import datetime
    proj_name = ir_data.get("meta", {}).get("projectName") or "Scratch project"
    targets = ir_data.get("targets", [])
    n_sprites = sum(1 for t in targets if not t.get("isStage"))
    n_stage = sum(1 for t in targets if t.get("isStage"))
    py_files = sorted(p.name for p in output_dir.glob("*.py"))
    readme = [
        "# Decompiled Scratch Project",
        "",
        "Generated by **decompile.py** — a Scratch SB3 → executable Python "
        "decompiler (IR parse, LLVM-style optimisation passes, then code emit).",
        "",
        "## Project info",
        "",
        f"- **Original project:** {proj_name}",
        f"- **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Sprites (non-stage):** {n_sprites}",
        f"- **Stage targets:** {n_stage}",
        f"- **Python modules emitted:** {len(py_files)}",
        f"- **Target FPS:** {opts.get('target_fps', 60.0)}",
        "",
        "## Files",
        "",
        "```",
    ]
    for fn in py_files:
        readme.append(f"  {fn}")
    readme.append("```")
    readme += [
        "",
        "## How to run",
        "",
        "```",
        "cd " + output_dir.name,
        "python main.py",
        "```",
        "",
        "This launches the game window plus a **Debug & Control Panel** "
        "(separate window) with:",
        "",
        "- **Transport:** green flag, pause/resume, stop, restart.",
        "- **Sprites tab:** live grid (position, z, costume, visible, "
        "ops/sec, effects, …); sortable; show/hide; z nudge.",
        "- **Z-Order tab:** drag to reorder draw order; double-click to front.",
        "- **Live Log tab:** timestamped engine events.",
        "- **Inspector tab:** deep per-sprite detail (costumes, variables, "
        "lists, procedures, position history).",  # noqa
        "- **Sound Mixer tab:** sounds with paths; right-click to open the "
        "file; open the decompiled folder.",  # noqa
        "- **Engine Stats tab:** fps, ops/sec, timing, memory.",
        "- **Export:** granular snapshot (CSV/JSON/log/history) with smart "
        "defaults — large values are truncated/summarised so files stay small.",
        "",
        "Right-click a sprite in the grid to open its active costume file; "
        "right-click a sound to open its file location.",
        "",
        "## Notes",
        "",
        "- All modules are auto-generated; re-run decompile.py to regenerate.",
        "- Asset files (costumes/sounds) live under `data/<target>/`.",
        "",
    ]
    (output_dir / "README.md").write_text("\n".join(readme), encoding="utf-8")
    log.info("emitted README.md")


_EXPR_FN = {
    "operator_add": "_add",
    "operator_subtract": "_sub",
    "operator_multiply": "_mul",
    "operator_divide": "_div",
    "operator_mod": "_mod",
    "operator_round": "_round",
    "operator_join": "_join",
    "operator_letter_of": "_letter_of",
    "operator_length": "_len",
    "operator_contains": "_contains",
    "operator_and": "_and",
    "operator_or": "_or",
    "operator_not": "_not",
    "operator_equals": "_eq",
    "operator_eq": "_eq",
    "operator_lt": "_lt",
    "operator_gt": "_gt",
    "operator_random": "_random",
    "operator_compare": "_eq",
    "operator_mathop": "_mathop",
}


class _PyEmitter:
    """Emit real Python source code from one target's IR."""

    def __init__(self, target, index, global_vars=None):
        self.t = target
        self.index = index
        self._for_each_counter = 0
        self._warp_loop_counter = 0
        self._warp = False
        # Prefix used for CSE temporaries in THIS target (collision-free).
        self.cse_prefix = target.get("cse_prefix", _CSE_VAR_PREFIX)
        # Names of variables OWNED by this target. Reads/writes of these can go
        # straight to the backing dict (sp.vars[...]) instead of routing through
        # Sprite.__getitem__/__setitem__, avoiding a method call + local/stage
        # fallback check on every access. Names not in this set (stage globals
        # referenced from a sprite, or dynamically-created names) keep the safe
        # sp[...] form which performs the local->stage fallback resolution.
        self._own_vars = frozenset(target.get("variables", {}).keys())
        # Canonical GLOBAL (stage-owned) variable names across the whole project.
        # The fast sp.vars[...] path is ONLY safe when this target canonically
        # owns the name (the stage, or a sprite-local that is NOT also a global).
        self._global_vars = frozenset(global_vars or ())
        # Names of custom procedures actually registered in sp._procs. Calls to
        # procedures stripped during build() (//-prefixed and zero-width-space
        # comment blocks) must be no-oped, else `yield from None` crashes.
        self._proc_names = frozenset(
            p.get("name") for p in target.get("procedures", []) if isinstance(p, dict))

    def _is_own_var(self, name):
        """True iff `name` is a variable this target canonically owns and may
        be accessed through the fast sp.vars[...] path (bypassing the
        Sprite.__getitem__/__setitem__ local->stage fallback dispatch)."""
        if name not in self._own_vars:
            return False
        if self.t.get("isStage"):
            return True
        return name not in self._global_vars

    # ---- entry point -------------------------------------------------------

    def emit(self):
        # Reset emitter state for multi-pass compiler reliability
        self._hat_names = {}
        self._hat_order = {}
        self._for_each_counter = 0
        self._warp_loop_counter = 0
        t = self.t
        header = self._emit_header()
        sprite_lines = []
        self._emit_sprite(sprite_lines)
        proc_lines = []
        for p in t.get("procedures", []):
            self._emit_procedure(proc_lines, p)
        hat_lines = []
        for h in t.get("hats", []):
            self._emit_hat(hat_lines, h)

        if getattr(self, "package", False):
            return self._assemble_package(header, sprite_lines, proc_lines, hat_lines)

        reg_lines = []
        self._emit_register(reg_lines)
        return "\n".join(header + [""] + sprite_lines + [""] + proc_lines
                         + [""] + hat_lines + [""] + reg_lines)

    def _pn(self, raw_name):
        return pyname(raw_name)

    def _emit_header(self):
        t = self.t
        lines = [
            f"# Auto-generated decompile of Scratch target {t['name']!r}",
            "# Generator: decompile.py  (no JSON blobs)",
            "import json, math, random, threading, time",
            "from typing import Any",
            "from _engine import (Engine, Sprite, _num, _str, _add, _sub,",
            "    _mul, _div, _mod, _eq, _lt, _gt, _not, _and, _or,",
            "    _random, _round, _mathop, _join, _letter_of, _len, _contains,",
            "    _list_index, _sensing_current, _sensing_days2000, _sensing_of,",
            "    _sensing_distanceto, _sensing_touchingobject, _sensing_touchingcolor, _sensing_coloristouchingcolor,",
            "    _display_clear, _display_stamp, _display_penup, _display_pendown,",
            "    _display_setcolor, _display_setcolor_num, _display_setsize,",
            "    _display_setpencolorparam, _display_changepencolorparam,",
            "    _load_list_asset)",
            "",
            "",
            "# module-level engine reference (set by register())",
            "_eng: Any = None",
            "",
            "# Turbowarp pseudo-booleans",
            "_is_turbowarp = False",
            "_is_compiled = False",
            "",
        ]
        return lines

    def _assemble_package(self, header, sprite_lines, proc_lines, hat_lines):
        """Large targets become a real package: __init__.py holds the sprite
        instance + register(); procs.py and hats.py hold the generated
        procedures and hat handlers respectively. register() injects the
        runtime _eng into the submodules so their functions can see it."""
        t = self.t
        _ = pyname(t["name"])
        # procs.py and hats.py each need the engine helpers + their own _eng
        sub_header = [
            f"# Auto-generated procedures for Scratch target {t['name']!r}",
            "import math, random, time, os as _os",
            "from typing import Any",
            "from _engine import (Engine, Sprite, _num, _str, _add, _sub,",
            "    _mul, _div, _mod, _eq, _lt, _gt, _not, _and, _or,",
            "    _random, _round, _mathop, _join, _letter_of, _len, _contains,",
            "    _list_index, _sensing_current, _sensing_days2000, _sensing_of,",
            "    _sensing_distanceto, _sensing_touchingobject, _sensing_touchingcolor, _sensing_coloristouchingcolor,",
            "    _display_clear, _display_stamp, _display_penup, _display_pendown,",
            "    _display_setcolor, _display_setcolor_num, _display_setsize,",
            "    _display_setpencolorparam, _display_changepencolorparam,",
            "    _load_list_asset)",
            "",
            "# runtime engine ref, injected by the package register()",
            "_eng: Any = None",
            "_is_turbowarp = False",
            "_is_compiled = False",
            "",
        ]
        procs_code = "\n".join(sub_header + [""] + proc_lines)
        hats_code = "\n".join(sub_header + [""] + hat_lines)

        # __init__.py: header + sprite + register() that wires submodules
        init_lines = list(header)
        init_lines += [""] + sprite_lines
        init_lines += ["", "", "def register(eng):"]
        init_lines += [
            "    global _eng",
            "    _eng = eng",
            "    # inject the engine into the submodules so their callables see it",
            "    from . import procs, hats",
            "    procs._eng = eng",
            "    hats._eng = eng",
            "    eng.sprites[sp.name] = sp",
            "    if sp._stage is None and eng.stage:",
            "        sp.attach_stage(eng.stage)",
        ]
        # procedures dict
        procs = t.get("procedures", [])
        if procs:
            init_lines.append("    sp._procs = {")
            for p in procs:
                init_lines.append(f"        {p['name']!r}: procs._proc_{self._pn(p['name'])}, ")
            init_lines.append("    }")
        # hats
        for hi, h in enumerate(t.get("hats", [])):
            ev = h["event"]
            etype = ev.get("type", "")
            if etype == "event_whenflagclicked":
                fname = "hat_green_flag"
            elif etype == "event_whenbroadcastreceived":
                fname = f"hat_bc_{self._pn(ev.get('broadcast', ''))}"
            elif etype == "event_whenkeypressed":
                fname = f"hat_key_{self._pn(ev.get('key', ''))}"
            elif etype == "event_whenstageclicked":
                fname = "hat_stage_clicked"
            elif etype == "control_start_as_clone":
                fname = "hat_clone"
            else:
                fname = f"hat_{self._pn(etype)}"
            if hasattr(self, '_hat_order') and fname in self._hat_order:
                entries = self._hat_order[fname]
                key = fname
                fname = entries.pop(0)
                if not entries:
                    del self._hat_order[key]
            ev_json = json.dumps(ev)
            init_lines.append(f"    sp.hats.append({{\"event\": {ev_json}, \"body_gen\": getattr(hats, {fname!r})}})")
        init_lines += [
            "    if sp.is_stage:",
            "        eng.set_stage(sp)",
            "    return sp",
            "",
        ]
        init_code = "\n".join(init_lines)
        return {"__init__.py": init_code, "procs.py": procs_code, "hats.py": hats_code}


    def _num_expr(self, node) -> str:
        """Emit a numeric coercion. If the node is a constant, emit the raw
        float literal directly (skips a _num() call + isinstance/coercion at
        runtime). Otherwise fall back to _num(<expr>)."""
        if isinstance(node, dict) and node.get("kind") == "const":
            v = node["v"]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return repr(float(v))
        return f"_num({self._expr(node)})"

    def _emit_sprite(self, lines):
        t = self.t
        big = getattr(self, "big_lists", {}) or {}
        vars_repr = json.dumps(t["variables"], indent=2)
        # split lists: small ones inline, big ones loaded lazily from data/
        small_lists = {k: v for k, v in t.get("lists", {}).items() if k not in big}
        lists_repr = json.dumps(small_lists, indent=2)
        lines.append(f"sp = Sprite({t['name']!r}, {t['isStage']},")
        lines.append(f"    {vars_repr},")
        lines.append(f"    {lists_repr},")
        lines.append(")")
        # lazily load externalised big lists (same contents, just off-source)
        if big:
            lines.append("")
            lines.append("# load large lists from external data assets")
            lines.append("import json as _json_mod")
            for lname, rel in big.items():
                lines.append(f"sp.lists[{lname!r}] = _load_list_asset({rel!r})")
        lines.append("")

    # ---- expression emitter ------------------------------------------------

    def _expr(self, node) -> str:
        """Emit a Python expression string from a value node."""
        if isinstance(node, (int, float)):
            return repr(node)
        if isinstance(node, str):
            return repr(node)
        if node is None:
            return "''"
        kind = node.get("kind")
        if kind == "const":
            v = node["v"]
            if isinstance(v, tuple) and v and v[0] == "__BROADCAST__":
                return repr(v[1])
            return json.dumps(v) if not isinstance(v, (int, float)) else repr(v)
        if kind == "var":
            # CSE variables are pure Python locals, not sprite properties
            if node['name'].startswith(self.cse_prefix):
                return node['name']
            # Inline-procedure temporaries are function-scoped Python locals.
            if node['name'].startswith("_inline_"):
                return node['name']
            # Fast path for this target's own canonically-owned variables: hit
            # the dict directly (identical to __getitem__'s first branch).
            if self._is_own_var(node['name']):
                return f"sp.vars[{node['name']!r}]"
            return f"sp[{node['name']!r}]"
        if kind == "list":
            return f"sp.get_list({node['name']!r})"
        if kind == "arg":
            _TW = {"is TurboWarp?": "_is_turbowarp", "is compiled?": "_is_compiled"}
            if node["name"] in _TW:
                return _TW[node["name"]]
            return self._pn(node["name"])
        if kind == "call":
            a = ", ".join(self._expr(a) for a in node.get("args", []))
            return f"sp.call_proc({node['name']!r}, [{a}])"
        if kind == "expr":
            return self._expr_op(node)
        return "0.0"

    def _expr_op(self, node) -> str:
        op = node["op"]
        a = node.get("args", {})
        f = node.get("fields", {})

        if op in _EXPR_FN:
            fn = _EXPR_FN[op]
            if op == "operator_mathop":
                oper = f.get("OPERATOR", "")
                return f"{fn}({oper!r}, {self._expr(a.get('NUM'))})"
            if op in ("operator_not", "operator_round", "operator_length"):
                key = {"operator_not": "OPERAND", "operator_round": "NUM",
                       "operator_length": "STRING"}[op]
                return f"{fn}({self._expr(a.get(key))})"
            # n-ary ops: chain calls for all args
            _nary_ops = frozenset({"operator_add", "operator_multiply", "operator_and", "operator_or"})
            vals = [self._expr(v) for v in a.values()]
            if op in _nary_ops and len(vals) > 2:
                # right-associative chain: fn(v0, fn(v1, fn(v2, ...)))
                result = vals[-1]
                for v in reversed(vals[:-1]):
                    result = f"{fn}({v}, {result})"
                return result
            # binary ops with ordered args (first two only)
            k1, k2 = list(a.keys())[:2] if a else ("", "")
            if op == "operator_letter_of":
                # Scratch: OPERAND1 = index, OPERAND2 = string
                return f"{fn}({self._expr(a.get(k2))}, {self._expr(a.get(k1))})"
            return f"{fn}({self._expr(a.get(k1))}, {self._expr(a.get(k2))})"

        if op == "sensing_dayssince2000":
            return "(_sensing_days2000())"

        if op in ("sensing_mousex", "sensing_mousey", "sensing_mousedown", "sensing_timer"):
            m = {"sensing_mousex": "_eng._mouse['x']",
                 "sensing_mousey": "_eng._mouse['y']",
                 "sensing_mousedown": "_eng._mouse['down']",
                 "sensing_timer": "time.time() - _eng._timer_start"}
            return m[op]

        if op == "sensing_keypressed":
            _key_expr = self._expr(a.get('KEY'))
            return f"(_str({_key_expr}) == 'any' and any(_eng._key_states.values())) or _eng._key_states.get(_str({_key_expr}), False)"

        if op == "sensing_keyoptions":
            return self._expr(a.get("KEY"))

        if op == "sensing_distancetomenu":
            return self._expr(a.get("DISTANCETOMENU"))

        if op == "sensing_current":
            unit = f.get("CURRENTMENU", "")
            return f"_sensing_current({unit!r})"

        if op == "sensing_distanceto":
            subj = self._expr(a.get('SUBJECT'))
            return f"(_sensing_distanceto(sp, {subj}))"

        if op == "sensing_touchingcolor":
            return f"(_sensing_touchingcolor(sp, {self._expr(a.get('COLOR'))}))"

        if op == "sensing_coloristouchingcolor":
            return f"(_sensing_coloristouchingcolor(sp, {self._expr(a.get('COLOR'))}, {self._expr(a.get('COLOR2'))}))"

        if op == "sensing_of":
            prop = f.get("PROPERTY", "")
            return f"_sensing_of(sp, {self._expr(a.get('OBJECT'))}, {prop!r})"

        if op in ("motion_xposition", "motion_yposition", "motion_direction"):
            m = {"motion_xposition": "sp.x", "motion_yposition": "sp.y",
                 "motion_direction": "((sp.direction + 180) % 360) - 180"}
            return m[op]

        if op == "looks_size":
            return "sp.size"

        if op == "looks_costumenumbername":
            # Check NUMBER_NAME field: "name" returns costume name, "number" returns index
            num_name = f.get("NUMBER_NAME", "number")
            if num_name == "name":
                return "(sp.costumes[sp._costume_index].get('name', '') if sp.costumes and 0 <= sp._costume_index < len(sp.costumes) else '')"
            return "(sp._costume_index + 1 if sp.costumes else 0)"

        if op == "looks_backdropnumbername":
            # Check NUMBER_NAME field: "name" returns backdrop name, "number" returns index
            num_name = f.get("NUMBER_NAME", "number")
            if num_name == "name":
                return "(_eng.stage.costumes[_eng.stage._costume_index].get('name', '') if _eng.stage and _eng.stage.costumes else '')"
            return "(_eng.stage._costume_index + 1 if _eng.stage else 1)"

        if op == "sound_volume":
            return "_eng.get_volume()"

        if op in ("sensing_loudness", "sensing_loud"):
            return "(_sensing_loudness())"

        if op in ("data_itemoflist", "data_itemnumoflist",
                  "data_lengthoflist", "data_listcontainsitem"):
            lst = f.get("LIST", "")
            if op == "data_itemoflist":
                return f"sp.list_item({lst!r}, {self._expr(a.get('INDEX'))})"
            if op == "data_itemnumoflist":
                return f"sp.list_index_of({lst!r}, {self._expr(a.get('ITEM'))})"
            if op == "data_lengthoflist":
                return f"sp.list_length({lst!r})"
            if op == "data_listcontainsitem":
                return f"sp.list_contains({lst!r}, {self._expr(a.get('ITEM'))})"

        if op == "key_pressed":
            return f"_eng._key_states.get(_str({self._expr(a.get('KEY'))}), False)"

        # sensing
        if op == "sensing_answer":
            return "_eng._answer"
        if op == "sensing_touchingobject":
            return f"_sensing_touchingobject(sp, {self._expr(a.get('TOUCHINGOBJECTMENU'))})"
        if op == "sensing_username":
            return "(_os.environ.get('USER', _os.environ.get('USERNAME', '')))"

        # looks reporters
        if op == "looks_costume":
            # The "costume (X)" reporter returns the literal costume name
            # given in its COSTUME field (used as the argument of
            # "switch costume to (costume (X))"). Returning the *current*
            # costume name here would turn every switch into a no-op.
            _cname = f.get("COSTUME")
            if isinstance(_cname, list) and _cname:
                return repr(_cname[0])
            return "sp.costumes[sp._costume_index].get('name', '') if sp.costumes else ''"
        if op == "looks_backdrops":
            _bname = f.get("COSTUME")
            if isinstance(_bname, list) and _bname:
                return repr(_bname[0])
            return "_eng.stage.costumes[_eng.stage._costume_index].get('name', '') if _eng.stage and _eng.stage.costumes else ''"

        # Turbowarp Counter extension
        if op == "control_get_counter":
            return "sp['__counter__']"

        # Unknown extension reporter — log warning at build time.
        # We cannot inline a comment here because expressions appear
        # inside function-call arguments where #-comments are illegal.
        if op:
            log.warning("unsupported extension reporter: %s", op)
        return "0.0"

    # ---- statement emitter -------------------------------------------------

    def _stmt(self, s, indent=0) -> str:
        op = s["op"]
        a = s.get("args", {})
        f = s.get("fields", {})
        pad = "  " * indent

        # data
        if op == "data_setvariableto":
            var_name = f['VARIABLE']
            # CSE variables are pure Python locals, not sprite properties
            if var_name.startswith(self.cse_prefix):
                return f"{pad}{var_name} = {self._expr(a.get('VALUE'))}"
            # Inline-procedure temporaries are function-scoped Python locals.
            if var_name.startswith("_inline_"):
                return f"{pad}{var_name} = {self._expr(a.get('VALUE'))}"
            # Fast path for this target's own canonically-owned variables.
            if self._is_own_var(var_name):
                return f"{pad}sp.vars[{var_name!r}] = {self._expr(a.get('VALUE'))}"
            return f"{pad}sp[{var_name!r}] = {self._expr(a.get('VALUE'))}"
        if op == "data_changevariableby":
            var_name = f['VARIABLE']
            if var_name.startswith(self.cse_prefix):
                return (f"{pad}{var_name} = "
                        f"_num({var_name}) + _num({self._expr(a.get('VALUE'))})")
            if var_name.startswith("_inline_"):
                return (f"{pad}{var_name} = "
                        f"_num({var_name}) + _num({self._expr(a.get('VALUE'))})")
            # Fast path for own vars: mirror Sprite.change_var but skip dispatch.
            if self._is_own_var(var_name):
                return (f"{pad}sp.vars[{var_name!r}] = "
                        f"_num(sp.vars[{var_name!r}]) + _num({self._expr(a.get('VALUE'))})")
            return f"{pad}sp.change_var({var_name!r}, {self._expr(a.get('VALUE'))})"
        if op == "data_addtolist":
            return f"{pad}sp.list_add({f['LIST']!r}, {self._expr(a.get('ITEM'))})"
        if op == "data_deleteoflist":
            return f"{pad}sp.list_delete({f['LIST']!r}, {self._expr(a.get('INDEX'))})"
        if op == "data_deletealloflist":
            return f"{pad}sp.list_delete_all({f['LIST']!r})"
        if op == "data_replaceitemoflist":
            return f"{pad}sp.list_replace({f['LIST']!r}, {self._expr(a.get('INDEX'))}, {self._expr(a.get('ITEM'))})"
        if op == "data_insertatlist":
            return f"{pad}sp.list_insert({f['LIST']!r}, {self._expr(a.get('INDEX'))}, {self._expr(a.get('ITEM'))})"

        # PATCH 14: collapsed list reset+adds → list literal assignment
        if op == "_list_direct_assign":
            items_str = ", ".join(self._expr(item) for item in a.get("ITEMS", []))
            return f"{pad}sp.lists[{f['LIST']!r}] = [{items_str}]"

        # control flow (these emit blocks with nesting)
        if op == "control_if":
            return self._emit_if(s, indent)
        if op == "control_if_else":
            return self._emit_if_else(s, indent)
        if op == "control_repeat":
            return self._emit_repeat(s, indent)
        if op == "control_repeat_until":
            return self._emit_repeat_until(s, indent)
        if op == "control_while":
            return self._emit_while(s, indent)
        if op == "control_forever":
            return self._emit_forever(s, indent)
        if op == "control_for_each":
            return self._emit_for_each(s, indent)
        if op == "control_wait":
            duration = self._expr(a.get('DURATION'))
            if self._warp:
                return f"{pad}# warp-mode: wait {duration} stripped (Scratch turns off warp on wait)"
            return (f"{pad}_wstart = time.time()\n"
                    f"{pad}while time.time() - _wstart < _num({duration}):\n"
                    f"{pad}    yield")
        if op == "control_wait_until":
            body = f"{pad}while not {self._expr(a.get('CONDITION'))}:\n{pad}  yield"
            if self._warp:
                return f"{pad}# warp-mode: wait until stripped (Scratch turns off warp on wait)"
            return body
        if op == "control_stop":
            mode = self._expr(f.get("STOP_OPTION"))
            if mode == "'all'":
                return f"{pad}return '__STOP_ALL__'"
            if mode == "'other scripts in sprite'":
                return f"{pad}_eng._stop_other_scripts(sp.name)"
            return f"{pad}return"
        if op == "control_start_as_clone":
            body = self._emit_body(s.get("sub", []), indent)
            y = self._yield(pad)
            return body + y
        if op == "control_create_clone_of":
            target = self._expr(a.get("CLONE_OPTION"))
            return f"{pad}_eng.create_clone_of({target})"
        if op == "control_delete_this_clone":
            return f"{pad}return '__DELETE_CLONE__'"

        # events
        if op == "event_broadcast":
            bc = a.get("BROADCAST_INPUT") or a.get("BROADCAST")
            return f"{pad}_eng.broadcast({self._expr(bc)})"
        if op == "event_broadcastandwait":
            bc = a.get("BROADCAST_INPUT") or a.get("BROADCAST")
            return f"{pad}yield from _eng.broadcast_and_wait({self._expr(bc)})"
        # procedures
        if op == "procedures_call":
            name = f.get("PROCCODE", a.get("PROCCODE", ""))
            # Comment-only (//-prefixed) and zero-width-space custom blocks are
            # stripped from sp._procs by build(), so a real call would evaluate
            # to `yield from None` and crash on frame 1. Emit a no-op yield
            # instead (yield, not pass, preserves frame boundaries in tight
            # loops that contained only these comment calls).
            if name.startswith("//") or "\u200b" in name or name not in self._proc_names:
                return f"{pad}yield  # no-op: stripped/dead proc {name!r}"
            args_list = ", ".join(self._expr(x) for x in a.get("_args", []))
            # Yield from the procedure generator to block the caller until completion
            if args_list:
                return f"{pad}yield from sp.call_proc_gen({name!r}, [{args_list}])"
            return f"{pad}yield from sp.call_proc_gen({name!r}, [])"
        if op == "procedures_definition":
            return ""

        # motion
        if op == "motion_setx":
            return (f"{pad}sp.x = {self._num_expr(a.get('X'))}\n"
                    f"{pad}if _eng and _eng.display: _eng.display.pen_move(sp, sp.x, sp.y)")
        if op == "motion_sety":
            return (f"{pad}sp.y = {self._num_expr(a.get('Y'))}\n"
                    f"{pad}if _eng and _eng.display: _eng.display.pen_move(sp, sp.x, sp.y)")
        if op == "motion_changexby":
            return (f"{pad}sp.x += {self._num_expr(a.get('DX'))}\n"
                    f"{pad}if _eng and _eng.display: _eng.display.pen_move(sp, sp.x, sp.y)")
        if op == "motion_changeyby":
            return (f"{pad}sp.y += {self._num_expr(a.get('DY'))}\n"
                    f"{pad}if _eng and _eng.display: _eng.display.pen_move(sp, sp.x, sp.y)")
        if op == "motion_gotoxy":
            return (f"{pad}sp.x, sp.y = {self._num_expr(a.get('X'))}, {self._num_expr(a.get('Y'))}\n"
                    f"{pad}if _eng and _eng.display:\n"
                    f"{pad}    _eng.display.pen_move(sp, sp.x, sp.y)\n"
                    f"{pad}    sp._px, sp._py = sp.x, sp.y")
        if op == "motion_movesteps":
            return (f"{pad}_r = math.radians(sp.direction)\n"
                    f"{pad}sp.x += {self._num_expr(a.get('STEPS'))} * math.sin(_r)\n"
                    f"{pad}sp.y += {self._num_expr(a.get('STEPS'))} * math.cos(_r)\n"
                    f"{pad}if _eng and _eng.display: _eng.display.pen_move(sp, sp.x, sp.y)")
        if op == "motion_turnright":
            return f"{pad}sp.direction = (sp.direction + {self._num_expr(a.get('DEGREES'))}) % 360"
        if op == "motion_turnleft":
            return f"{pad}sp.direction = (sp.direction - {self._num_expr(a.get('DEGREES'))}) % 360"
        if op == "motion_goto":
            tgt = self._expr(a.get('TOWARDS'))
            return (f"{pad}_gt = {tgt}\n"
                    f"{pad}_ox, _oy = sp.x, sp.y\n"
                    f"{pad}if _gt == '_mouse_':\n"
                    f"{pad}    sp.x, sp.y = _eng._mouse['x'], _eng._mouse['y']\n"
                    f"{pad}elif _gt == '_random_':\n"
                    f"{pad}    sp.x, sp.y = _random(-240, 240), _random(-180, 180)\n"
                    f"{pad}elif _gt == '_stage_':\n"
                    f"{pad}    sp.x, sp.y = 0.0, 0.0\n"
                    f"{pad}else:\n"
                    f"{pad}    _gs = _eng.sprites.get(_str(_gt))\n"
                    f"{pad}    if _gs is not None:\n"
                    f"{pad}        sp.x, sp.y = _gs.x, _gs.y\n"
                    f"{pad}if _eng and _eng.display and (sp.x != _ox or sp.y != _oy):\n"
                    f"{pad}    _eng.display.pen_move(sp, sp.x, sp.y)")
        if op == "motion_pointindirection":
            return f"{pad}sp.direction = {self._num_expr(a.get('DIRECTION'))} % 360"
        if op == "motion_pointtowards":
            tgt = self._expr(a.get('TOWARDS'))
            return (f"{pad}_pt = {tgt}\n"
                    f"{pad}if _pt == '_mouse_':\n"
                    f"{pad}    _tx, _ty = _eng._mouse['x'], _eng._mouse['y']\n"
                    f"{pad}elif _pt == '_stage_':\n"
                    f"{pad}    _tx, _ty = 0.0, 0.0\n"
                    f"{pad}else:\n"
                    f"{pad}    _gs = _eng.sprites.get(_str(_pt))\n"
                    f"{pad}    _tx, _ty = (_gs.x, _gs.y) if _gs is not None else (sp.x, sp.y)\n"
                    f"{pad}sp.direction = (90 - math.degrees(math.atan2(_ty - sp.y, _tx - sp.x))) % 360")
        if op == "motion_setrotationstyle":
            return f"{pad}sp.rotation_style = {f.get('STYLE')!r}"
        if op == "motion_ifonedgebounce":
            return f"{pad}if _eng and _eng.display: _eng.display.if_on_edge_bounce(sp)"

        # glide blocks (wall-clock paced; suppressed inside warp mode)
        if op == "motion_glidesecstoxy":
            secs = self._num_expr(a.get('SECS'))
            x_expr = self._num_expr(a.get('X'))
            y_expr = self._num_expr(a.get('Y'))
            _yield_line = "" if self._warp else f"{pad}    if not self._warp: yield"
            return (f"{pad}_gx, _gy = {x_expr}, {y_expr}\n"
                    f"{pad}_gs = max(0.016, {secs})\n"
                    f"{pad}_sx, _sy = sp.x, sp.y\n"
                    f"{pad}_gstart = time.time()\n"
                    f"{pad}_gmax = max(2, int(_gs * (getattr(_eng, 'target_fps', 30) or 30)) + 2)\n"
                    f"{pad}for _gi in range(_gmax):\n"
                    f"{pad}    _gel = time.time() - _gstart\n"
                    f"{pad}    _t = min(1.0, _gel / _gs)\n"
                    f"{pad}    sp.x = _sx + (_gx - _sx) * _t\n"
                    f"{pad}    sp.y = _sy + (_gy - _sy) * _t\n"
                    f"{pad}    if _eng and _eng.display: _eng.display.pen_move(sp, sp.x, sp.y)\n"
                    f"{pad}    if _t >= 1.0: break\n"
                    f"{_yield_line}")
        if op == "motion_glideto":
            secs = self._num_expr(a.get('SECS'))
            tgt = self._expr(a.get('TOWARDS'))
            _yield_line = "" if self._warp else f"{pad}    if not self._warp: yield"
            return (f"{pad}_gtgt = {tgt}\n"
                    f"{pad}if _gtgt == '_mouse_': _gx, _gy = _eng._mouse['x'], _eng._mouse['y']\n"
                    f"{pad}elif _gtgt == '_random_': _gx, _gy = _random(-240, 240), _random(-180, 180)\n"
                    f"{pad}elif _gtgt == '_stage_': _gx, _gy = 0.0, 0.0\n"
                    f"{pad}else: _gs2 = _eng.sprites.get(_str(_gtgt)); _gx, _gy = (_gs2.x, _gs2.y) if _gs2 is not None else (sp.x, sp.y)\n"
                    f"{pad}_gs = max(0.016, {secs})\n"
                    f"{pad}_sx, _sy = sp.x, sp.y\n"
                    f"{pad}_gstart = time.time()\n"
                    f"{pad}_gmax = max(2, int(_gs * (getattr(_eng, 'target_fps', 30) or 30)) + 2)\n"
                    f"{pad}for _gi in range(_gmax):\n"
                    f"{pad}    _gel = time.time() - _gstart\n"
                    f"{pad}    _t = min(1.0, _gel / _gs)\n"
                    f"{pad}    sp.x = _sx + (_gx - _sx) * _t\n"
                    f"{pad}    sp.y = _sy + (_gy - _sy) * _t\n"
                    f"{pad}    if _eng and _eng.display: _eng.display.pen_move(sp, sp.x, sp.y)\n"
                    f"{pad}    if _t >= 1.0: break\n"
                    f"{_yield_line}")

        # looks
        if op == "looks_switchcostumeto":
            _cexpr = self._expr(a.get('COSTUME'))
            # No-op: switching to the sprite's *current* costume name is a
            # redundant O(n) scan that changes nothing (empty costume menu
            # field resolves to the current-costume self-reference below).
            if _cexpr == "sp.costumes[sp._costume_index].get('name', '') if sp.costumes else ''":
                return ""
            return f"{pad}sp.set_costume({_cexpr})"
        if op == "looks_nextcostume":
            return f"{pad}sp.set_costume(sp._costume_index + 2)"
        if op == "looks_setsizeto":
            _sz_node = a.get('SIZE')
            if isinstance(_sz_node, dict) and _sz_node.get('kind') == 'const':
                _folded = max(1.0, float(_sz_node['v']))
                return f"{pad}sp.size = {_folded}"
            return f"{pad}sp.size = max(1.0, {self._num_expr(_sz_node)})"
        if op == "looks_show":
            return f"{pad}sp.visible = True"
        if op == "looks_hide":
            return f"{pad}sp.visible = False"
        if op == "looks_say":
            msg = a.get("MESSAGE")
            if msg is None:
                return f"{pad}sp._say_text = ''"
            return f"{pad}sp._say_text = _str({self._expr(msg)}); sp._say_type = \"say\""
        if op == "looks_think":
            msg = a.get("MESSAGE")
            if msg is None:
                return f"{pad}sp._say_text = ''"
            return f"{pad}sp._say_text = _str({self._expr(msg)}); sp._say_type = \"think\""
        if op == "looks_sayforsecs":
            msg = a.get("MESSAGE")
            if msg is None:
                return f"{pad}sp._say_text = ''\n{pad}yield"
            return f"{pad}sp._say_text = _str({self._expr(msg)}); sp._say_type = \"say\"\n{pad}yield"
        if op == "looks_thinkforsecs":
            msg = a.get("MESSAGE")
            if msg is None:
                return f"{pad}sp._say_text = ''\n{pad}yield"
            return f"{pad}sp._say_text = _str({self._expr(msg)}); sp._say_type = \"think\"\n{pad}yield"
        if op == "looks_seteffectto":
            _eff = f['EFFECT'].lower()
            _val = self._num_expr(a.get('VALUE'))
            return f"{pad}sp._effects[{_eff!r}] = max(0.0, min(100.0, {_val})) if {_eff!r} == 'ghost' else {_val}"
        if op == "looks_changeeffectby":
            _eff = f['EFFECT'].lower()
            _ch = self._num_expr(a.get('CHANGE'))
            return f"{pad}_e = {_eff!r}; _v = sp._effects.get(_e, 0.0) + {_ch}; sp._effects[_e] = max(0.0, min(100.0, _v)) if _e == 'ghost' else _v"
        if op == "looks_cleareffects":
            return f"{pad}for _k in sp._effects: sp._effects[_k] = 0"
        if op == "looks_switchbackdropto":
            return f"{pad}if _eng.stage is not None: _eng.stage.set_costume({self._expr(a.get('BACKDROP'))})"
        if op == "looks_nextbackdrop":
            return f"{pad}if _eng.stage is not None: _eng.stage.set_costume(_eng.stage._costume_index + 2)"
        if op == "looks_switchbackdroptoandwait":
            back = self._expr(a.get("BACKDROP"))
            return (f"{pad}if _eng and _eng.display: _eng.display.switch_backdrop({back})\n"
                    f"{pad}yield")
        if op == "looks_gotofrontback":
            fb = self._expr(a.get("FRONT_BACK") or f.get("FRONT_BACK"))
            return f"{pad}sp.go_to_front_back({fb})"
        if op == "looks_goforwardbackwardlayers":
            fb = self._expr(a.get("FORWARD_BACKWARD") or f.get("FORWARD_BACKWARD"))
            num = self._expr(a.get("NUM"))
            return f"{pad}sp.go_forward_backward_layers({fb}, {num})"

        # pen (with display hook)
        if op == "pen_clear":
            return f"{pad}_display_clear()"
        if op == "pen_stamp":
            return f"{pad}_display_stamp(sp, sp.x, sp.y, sp.size)"
        if op == "pen_penUp":
            return f"{pad}_display_penup(sp)"
        if op == "pen_penDown":
            return f"{pad}_display_pendown(sp)"
        if op == "pen_setPenColorToColor":
            return f"{pad}_display_setcolor(sp, {self._expr(a.get('COLOR'))})"
        if op == "pen_setPenColorToNum":
            return f"{pad}_display_setcolor_num(sp, {self._expr(a.get('COLOR'))})"
        if op == "pen_setPenSizeTo":
            return f"{pad}_display_setsize(sp, {self._num_expr(a.get('SIZE'))})"
        if op == "pen_setPenColorParamTo":
            return f"{pad}_display_setpencolorparam(sp, {self._expr(a.get('COLOR_PARAM'))}, {self._expr(a.get('VALUE'))})"
        if op == "pen_changePenColorParamBy":
            return f"{pad}_display_changepencolorparam(sp, {self._expr(a.get('COLOR_PARAM'))}, {self._expr(a.get('VALUE'))})"

        # sound
        if op == "sound_play":
            return f"{pad}_eng.play_sound({self._expr(a.get('SOUND') or a.get('SOUND_MENU'))})"
        if op == "sound_playuntildone":
            return f"{pad}yield from _eng.play_sound_until_done({self._expr(a.get('SOUND') or a.get('SOUND_MENU'))})"
        if op == "sound_setvolumeto":
            return f"{pad}sp.volume = max(0.0, min(100.0, {self._num_expr(a.get('VOLUME'))}))"
        if op == "sound_changevolumeby":
            return f"{pad}sp.volume = max(0.0, min(100.0, sp.volume + {self._num_expr(a.get('VOLUME'))}))"
        if op == "sound_stopallsounds":
            return f"{pad}_eng.stop_sounds()"
        if op == "sound_cleareffects":
            return f"{pad}sp._sound_effects = {{}}"

        if op == "sensing_resettimer":
            return f"{pad}_eng._timer_start = time.time()"
        if op == "sensing_setdragmode":
            return f"{pad}sp.drag_mode = {self._expr(a.get('DRAG_MODE'))}"

        # sensing ask and wait (non-blocking overlay entry; yields until answered)
        if op == "sensing_askandwait":
            question = self._expr(a.get('QUESTION'))
            return f"{pad}_eng._answer = _str((yield from _eng.ask_and_wait({question})))"

        # data show/hide (UI operations — no-ops for now)
        if op == "data_showvariable":
            return f"{pad}pass  # data_showvariable: {f.get('VARIABLE', '?')}"
        if op == "data_hidevariable":
            return f"{pad}pass  # data_hidevariable: {f.get('VARIABLE', '?')}"
        if op == "data_hidelist":
            return f"{pad}pass  # data_hidelist: {f.get('LIST', '?')}"

        # looks clear graphic effects
        if op == "looks_cleargraphiceffects":
            return f"{pad}for _k in sp._effects: sp._effects[_k] = 0"

        # sound set effect (pitch / pan)
        if op == "sound_seteffectto":
            return f"{pad}_eng._sound_effects[{f.get('EFFECT', '').lower()!r}] = {self._num_expr(a.get('VALUE'))}"

        # Turbowarp Counter extension
        if op == "control_clear_counter":
            return f"{pad}sp['__counter__'] = 0"
        if op == "control_incr_counter":
            return f"{pad}sp['__counter__'] = _num(sp['__counter__']) + 1"

        # Unknown extension statement — log and emit a comment
        if op:
            log.warning("unsupported extension statement: %s", op)
            return f"{pad}# UNSUPPORTED STATEMENT: {op}"
        return ""

    # ---- control flow emitters ---------------------------------------------

    def _emit_body(self, body, indent=0):
        lines = []
        for s in body:
            emitted = self._stmt(s, indent)
            if emitted:
                lines.append(emitted)
        return "\n".join(lines) + ("\n" if lines else "")

    def _emit_if(self, s, indent):
        pad = "  " * indent
        cond = self._expr(s.get("args", {}).get("CONDITION"))
        body = self._emit_body(s.get("sub", []), indent + 1)
        result = f"{pad}if {cond}:\n{body}"
        if not body.strip():
            result += f"{pad}  pass\n"
        return result.rstrip()

    def _emit_if_else(self, s, indent):
        pad = "  " * indent
        cond = self._expr(s.get("args", {}).get("CONDITION"))
        body = self._emit_body(s.get("sub", []), indent + 1)
        else_body = self._emit_body(s.get("sub2", []), indent + 1)
        result = f"{pad}if {cond}:\n{body}"
        if not body.strip():
            result += f"{pad}  pass\n"
        if else_body.strip():
            result += f"{pad}else:\n{else_body}"
        else:
            result += f"{pad}else:\n{pad}  pass\n"
        return result.rstrip()

    def _body_has_wait(self, body):
        """Return True if body contains any wait blocks that require yielding."""
        for stmt in body:
            if isinstance(stmt, dict):
                # BUG FIX: IR nodes use the key "op", not "opcode". The
                # previous code always returned "" and every for_each
                # collapsed into a synchronous, non-yielding run.
                op = stmt.get("op", "")
                if op in _YIELD_INDUCING_OPS:
                    return True
                # recurse into sub-statements
                for key in ("sub", "sub2", "SUBSTACK", "SUBSTACK2"):
                    if key in stmt and self._body_has_wait(stmt[key]):
                        return True
        return False

    def _yield(self, pad):
        # In Scratch, forever/repeat/while loop one iteration per frame.
        # We yield once per iteration so the engine can advance frames.
        # Callers pass the full indentation (pad + body-level prefix).
        return f"{pad}yield"

    def _emit_repeat(self, s, indent):
        pad = "  " * indent
        times = self._expr(s.get("args", {}).get("TIMES"))
        body = self._emit_body(s.get("sub", []), indent + 1)
        if not body.strip():
            body = f"{pad}  pass\n"
        # Scratch cooperative semantics: a non-warp bounded loop ticks exactly
        # ONE iteration per frame regardless of whether it contains a wait.
        # Always yield once per iteration (warp loops run synchronously instead).
        y = "\n" + f"{pad}  yield" if not self._warp else ""
        return f"{pad}for _ in range(int(_num({times}))):\n{body}{y}".rstrip()

    def _emit_repeat_until(self, s, indent):
        pad = "  " * indent
        cond = self._expr(s.get("args", {}).get("CONDITION"))
        body = self._emit_body(s.get("sub", []), indent + 1)
        if self._warp:
            self._warp_loop_counter += 1
            w_var = f"_warp_i{self._warp_loop_counter}"
            result = f"{pad}_wt{self._warp_loop_counter} = time.perf_counter()\n{pad}for {w_var} in range(_eng._warp_iter_limit):\n{pad}  if {w_var} % max(1, _eng._warp_check_stride) == 0 and time.perf_counter() - _wt{self._warp_loop_counter} > _eng.warp_time_limit > 0:\n{pad}    _wt{self._warp_loop_counter} = time.perf_counter(); yield; continue\n{pad}  if {cond}: break\n{body}"
        else:
            # Unbounded loops MUST yield every iteration: the condition depends
            # on external/time state, so running synchronously would hang.
            y = f"{pad}  yield\n"
            result = f"{pad}while not {cond}:\n{body}{y}"
        if not body.strip() and not self._warp:
            result = f"{pad}while not {cond}:\n{pad}  pass"
        return result.rstrip()

    def _emit_while(self, s, indent):
        pad = "  " * indent
        cond = self._expr(s.get("args", {}).get("CONDITION"))
        body = self._emit_body(s.get("sub", []), indent + 1)
        if not body.strip():
            body = f"{pad}  pass\n"
        if self._warp:
            self._warp_loop_counter += 1
            w_var = f"_warp_i{self._warp_loop_counter}"
            return f"{pad}_wt{self._warp_loop_counter} = time.perf_counter()\n{pad}for {w_var} in range(_eng._warp_iter_limit):\n{pad}  if {w_var} % max(1, _eng._warp_check_stride) == 0 and time.perf_counter() - _wt{self._warp_loop_counter} > _eng.warp_time_limit > 0:\n{pad}    _wt{self._warp_loop_counter} = time.perf_counter(); yield; continue\n{pad}  if not ({cond}): break\n{body}".rstrip()
        # Unbounded loops MUST yield every iteration to avoid hangs.
        y = f"{pad}  yield\n"
        return f"{pad}while {cond}:\n{body}{y}".rstrip()

    def _emit_forever(self, s, indent):
        pad = "  " * indent
        body = self._emit_body(s.get("sub", []), indent + 1)
        if not body.strip():
            body = f"{pad}  pass\n"
        if self._warp:
            self._warp_loop_counter += 1
            w_var = f"_warp_i{self._warp_loop_counter}"
            return f"{pad}_wt{self._warp_loop_counter} = time.perf_counter()\n{pad}for {w_var} in range(_eng._warp_iter_limit):\n{pad}  if {w_var} % max(1, _eng._warp_check_stride) == 0 and time.perf_counter() - _wt{self._warp_loop_counter} > _eng.warp_time_limit > 0:\n{pad}    _wt{self._warp_loop_counter} = time.perf_counter(); yield; continue\n{body}"
        # 'forever' is infinite; it MUST yield every iteration.
        y = f"{pad}  yield\n"
        return f"{pad}while True:\n{body}{y}"

    def _emit_for_each(self, s, indent):
        pad = "  " * indent
        var = s.get("fields", {}).get("VARIABLE", "")
        seq = self._expr(s.get("args", {}).get("VALUE"))
        body = self._emit_body(s.get("sub", []), indent + 1)
        self._for_each_counter += 1
        n = self._for_each_counter
        result = f"{pad}_sv{n} = {seq}\n"
        result += f"{pad}if isinstance(_sv{n}, list):\n"
        result += f"{pad}  _it{n} = _sv{n}\n"
        result += f"{pad}else:\n"
        result += f"{pad}  _nn{n} = int(_num(_sv{n}))\n"
        result += f"{pad}  _it{n} = range(1, _nn{n} + 1) if _nn{n} > 0 else []\n"
        result += f"{pad}for _itm{n} in _it{n}:\n"
        result += f"{pad}  sp[{var!r}] = _itm{n}\n"
        result += body
        # Scratch cooperative semantics: a non-warp for_each ticks exactly one
        # iteration per frame, so always yield once per iteration (unless warp).
        if not self._warp:
            result += self._yield(pad + "  ")
        return result

    # ---- procedure / hat emitters ------------------------------------------

    def _emit_procedure(self, lines, proc):
        name = proc["name"]
        args_list = ", ".join(self._pn(a) for a in proc.get("args", []))
        proc_params = f"sp{', ' + args_list if args_list else ''}"
        lines.append(f"def _proc_{self._pn(name)}({proc_params}):")
        prev_warp = getattr(self, '_warp', False)
        self._warp = proc.get("warp", False) or prev_warp
        body = self._emit_body(proc.get("body", []), 1)
        self._warp = prev_warp
        if body.strip():
            lines.append(body)
        else:
            lines.append("    pass")
        lines.append("")

    def _emit_hat(self, lines, hat):
        ev = hat["event"]
        etype = ev.get("type", "")
        body = hat.get("body", [])

        # determine hat function name
        if etype == "event_whenflagclicked":
            fname = "hat_green_flag"
        elif etype == "event_whenbroadcastreceived":
            bc = ev.get("broadcast", "")
            fname = f"hat_bc_{self._pn(bc)}"
        elif etype == "event_whenkeypressed":
            key = ev.get("key", "")
            fname = f"hat_key_{self._pn(key)}"
        elif etype == "event_whenstageclicked":
            fname = "hat_stage_clicked"
        elif etype == "control_start_as_clone":
            fname = "hat_clone"
        else:
            fname = f"hat_{self._pn(etype)}"

        # Deduplicate function names (collisions cause silent overwrites)
        if not hasattr(self, '_hat_names'):
            self._hat_names = {}
            self._hat_order = {}
        base = fname
        idx = 2
        while fname in self._hat_names:
            fname = f"{base}_{idx}"
            idx += 1
        self._hat_names[fname] = True
        self._hat_order.setdefault(base, []).append(fname)

        lines.append(f"def {fname}(sp):")
        emitted = self._emit_body(body, 1)
        if emitted.strip():
            lines.append(emitted)
        else:
            lines.append("    pass")
        lines.append("")

    # ---- register ----------------------------------------------------------

    def _emit_register(self, lines):
        lines.append("def register(eng):")
        lines.append("    global _eng")
        lines.append("    _eng = eng")
        lines.append("    eng.sprites[sp.name] = sp")
        lines.append("    if sp._stage is None and eng.stage:")
        lines.append("        sp.attach_stage(eng.stage)")

        # register procedures in a dict
        procs = self.t.get("procedures", [])
        if procs:
            lines.append("    sp._procs = {")
            for p in procs:
                lines.append(f"        {p['name']!r}: _proc_{self._pn(p['name'])}, ")
            lines.append("    }")

        # register hats
        hats = self.t.get("hats", [])
        for hi, h in enumerate(hats):
            ev = h["event"]
            etype = ev.get("type", "")
            if etype == "event_whenflagclicked":
                fname = "hat_green_flag"
            elif etype == "event_whenbroadcastreceived":
                fname = f"hat_bc_{self._pn(ev.get('broadcast', ''))}"
            elif etype == "event_whenkeypressed":
                fname = f"hat_key_{self._pn(ev.get('key', ''))}"
            elif etype == "event_whenstageclicked":
                fname = "hat_stage_clicked"
            elif etype == "control_start_as_clone":
                fname = "hat_clone"
            else:
                fname = f"hat_{self._pn(etype)}"
            # Use deduplicated name if one was assigned during emission
            if hasattr(self, '_hat_order') and fname in self._hat_order:
                entries = self._hat_order[fname]
                key = fname
                fname = entries.pop(0)
                if not entries:
                    del self._hat_order[key]

            ev_json = json.dumps(ev)
            lines.append(f"    sp.hats.append({{\"event\": {ev_json}, \"body_gen\": {fname}}})")

        lines.append("    if sp.is_stage:")
        lines.append("        eng.set_stage(sp)")
        lines.append("    return sp")
        lines.append("")


# ===================================================================
#  SHARED MODULE GENERATORS  (_engine.py, _display.py, main.py)
# ===================================================================

def _generate_engine(output_dir, opts=None):
    """Generate _engine.py with Sprite, Engine, and operator helpers."""
    if opts is None:
        opts = {}
    target_fps = opts.get("target_fps", 60.0)
    debug = opts.get("debug", True)
    output_dir = Path(output_dir)
    lines = [
        "# Auto-generated engine runtime",
        "# Generator: decompile.py",
        "from __future__ import annotations",
        "import datetime, math, random, threading, time",
        "from collections import deque",
        "from pathlib import Path",
        "from typing import Any, Dict, List, Optional",
        "",
        "",
        "# module-level engine / display references (set by Engine.init)",
        "_eng: Any = None",
        "_disp: Any = None",
        "",
        "",
        "# -------------------------------------------------------------------",
        "# value coercion (Scratch is loosely typed)",
        "# -------------------------------------------------------------------",
        "",
        "def _num(v: Any) -> float:",
        "    if isinstance(v, bool):",
        "        return 1.0 if v else 0.0",
        "    if isinstance(v, (int, float)):",
        "        return float(v)",
        "    if isinstance(v, str):",
        "        s = v.strip()",
        "        try:",
        "            return float(s)",
        "        except ValueError:",
        "            try:",
        "                return float(int(s, 0))",
        "            except (ValueError, TypeError):",
        "                return 0.0",
        "    return 0.0",
        "",
        "def _str(v: Any) -> str:",
        "    if isinstance(v, list):",
        '        return " ".join(_str(x) for x in v)',
        "    if isinstance(v, bool):",
        '        return "true" if v else "false"',
        "    if isinstance(v, float):",
        "        return str(int(v)) if v == int(v) else repr(v)",
        '    return "" if v is None else str(v)',
        "",
        "def _add(a: Any, b: Any) -> Any:",
        "    return _num(a) + _num(b)",
        "",
        "def _sub(a: Any, b: Any) -> Any:",
        "    return _num(a) - _num(b)",
        "",
        "def _mul(a: Any, b: Any) -> Any:",
        "    return _num(a) * _num(b)",
        "",
        "def _div(a: Any, b: Any) -> Any:",
        "    d = _num(b)",
        "    if d == 0:",
        "        return float('inf') if _num(a) >= 0 else float('-inf')",
        "    return _num(a) / d",
        "",
        "def _mod(a: Any, b: Any) -> Any:",
        "    m = _num(b)",
        "    return 0.0 if m == 0 else _num(a) % m",
        "",
        "def _is_numeric(v: Any) -> bool:",
        "    if isinstance(v, bool):",
        "        return True",
        "    if isinstance(v, (int, float)):",
        "        return True",
        "    if isinstance(v, str):",
        "        s = v.strip()",
        "        if s == '':",
        "            return False",
        "        try:",
        "            float(s)",
        "            return True",
        "        except (ValueError, TypeError):",
        "            return False",
        "    return False",
        "",
        "def _eq(a: Any, b: Any) -> bool:",
        "    if _is_numeric(a) and _is_numeric(b):",
        "        return _num(a) == _num(b)",
        "    return str(a).lower() == str(b).lower()",
        "",
        "def _lt(a: Any, b: Any) -> bool:",
        "    if _is_numeric(a) and _is_numeric(b):",
        "        return _num(a) < _num(b)",
        "    return str(a).lower() < str(b).lower()",
        "",
        "def _gt(a: Any, b: Any) -> bool:",
        "    if _is_numeric(a) and _is_numeric(b):",
        "        return _num(a) > _num(b)",
        "    return str(a).lower() > str(b).lower()",
        "",
        "def _and(a: Any, b: Any) -> bool:",
        "    return bool(a) and bool(b)",
        "",
        "def _or(a: Any, b: Any) -> bool:",
        "    return bool(a) or bool(b)",
        "",
        "def _not(a: Any) -> bool:",
        "    return not bool(a)",
        "",
        "def _random(a: Any, b: Any) -> float:",
        "    lo, hi = _num(a), _num(b)",
        "    if lo > hi:",
        "        lo, hi = hi, lo",
        "    # Scratch: if either original input had a decimal point, return float",
        "    if '.' not in str(a) and '.' not in str(b):",
        "        return float(random.randint(int(lo), int(hi)))",
        "    return lo + random.random() * (hi - lo)",
        "",
        "def _round(v: Any) -> float:",
        "    try:",
        "        return round(_num(v))",
        "    except (OverflowError, ValueError):",
        "        return 0.0",
        "",
"def _mathop(fn: str, x: float) -> float:",
        '    import math',
        '    fn = fn.lower()',
        '    if fn == "pi":',
        '        return math.pi',
        '    if fn == "e":',
        '        return math.e',
        '    # Trig functions with exact-value correction per Scratch spec',
        '    if fn in ("sin", "cos", "tan"):',
        '        # Normalize angle to [0, 360) for exact checks',
        '        angle = x % 360.0',
        '        if fn == "sin":',
        '            if angle in (0.0, 180.0): return 0.0',
        '            if angle == 90.0: return 1.0',
        '            if angle == 270.0: return -1.0',
        '        elif fn == "cos":',
        '            if angle in (90.0, 270.0): return 0.0',
        '            if angle == 0.0: return 1.0',
        '            if angle == 180.0: return -1.0',
        '        elif fn == "tan":',
        '            if angle in (0.0, 180.0): return 0.0',
        '            if angle == 90.0: return float("inf")',
        '            if angle == 270.0: return float("-inf")',
        '        return math.sin(math.radians(x)) if fn=="sin" else math.cos(math.radians(x)) if fn=="cos" else math.tan(math.radians(x))',
        '    trig = {\'asin\': math.asin, \'acos\': math.acos, \'atan\': math.atan}',
        '    if fn in trig:',
        '        try:',
        '            val = trig[fn](x)',
        '            if fn in (\'asin\', \'acos\', \'atan\'):',
        '                return math.degrees(val)',
        '            return val',
        '        except Exception:',
        '            # Out-of-domain (e.g. asin(2)) -> NaN, matching Scratch.',
        '            return float("nan")',
        '    table = {',
        '        "abs": abs, "floor": math.floor, "ceiling": math.ceil,',
        '        "sqrt": lambda v: math.sqrt(v) if v >= 0 else float("nan"),',
        '        "ln": lambda v: math.log(v) if v > 0 else (float("-inf") if v == 0 else float("nan")),',
        '        "log": lambda v: math.log10(v) if v > 0 else (float("-inf") if v == 0 else float("nan")),',
        '        "e ^": math.exp,',
        '        "10 ^": lambda v: 10 ** v, "round": round,',
        '    }',
        '    if fn in table:',
        '        try:',
        '            return table[fn](x)',
        '        except Exception:',
        '            return float("nan")',
        '    return float("nan")',
        "",
        "def _join(a: Any, b: Any) -> str:",
        "    return _str(a) + _str(b)",
        "",
        "class _str_acc:",
        "    '''String accumulator for chained join() calls.'''",
        "    __slots__ = ('_parts',)",
        "    def __init__(self, s=''):",
        "        self._parts = [s] if s else []",
        "    def __add__(self, other):",
        "        if isinstance(other, _str_acc):",
        "            self._parts.extend(other._parts)",
        "        else:",
        "            self._parts.append(_str(other))",
        "        return self",
        "    def __radd__(self, other):",
        "        self._parts.insert(0, _str(other))",
        "        return self",
        "    def __str__(self):",
        "        return ''.join(self._parts)",
        "    def __repr__(self):",
        "        return f'_str_acc({self._parts!r})'",
        "",
        'def _letter_of(s: Any, i: Any) -> str:',
        "    s, idx = _str(s), int(_num(i))",
        "    if 1 <= idx <= len(s):",
        "        return s[idx - 1]",
        '    return ""',
        "",
        "def _len(s: Any) -> int:",
        "    return len(_str(s))",
        "",
        "def _contains(s: Any, sub: Any) -> bool:",
        "    return _str(sub) in _str(s)",
        "",
        "def _list_index(idx: Any, lst: List, allow_append: bool = False) -> Optional[int]:",
        "    # Fast path: integer index (the common case, and the only case the",
        "    # compiler emits for statically-constant indices) avoids the string",
        "    # keyword checks and numeric re-coercion entirely.",
        "    _t = type(idx)",
        "    if _t is int:",
        "        i = idx",
        "    elif _t is str:",
        '        if idx == "last":',
        "            return len(lst) - 1",
        '        if idx == "all":',
        "            return None",
        '        if idx == "random":',
        "            return random.randint(0, len(lst) - 1) if lst else None",
        '        i = int(_num(idx)) if idx != "" else 1',
        "    else:",
        '        i = int(_num(idx)) if idx not in (None, "") else 1',
        "    limit = len(lst) + 1 if allow_append else len(lst)",
        "    if i < 1 or i > limit:",
        "        return None",
        "    return i - 1",
        "",
        "",
        "# -------------------------------------------------------------------",
        "# Sprite  (holds per-sprite state)",
        "# -------------------------------------------------------------------",
        "",
        "class Sprite:",
        "    def __init__(self, name: str, is_stage: bool,",
        "                 variables: Dict[str, Any], lists: Dict[str, List]):",
        "        self.name = name",
        "        self.is_stage = is_stage",
        "        self.vars: Dict[str, Any] = dict(variables)",
        "        self.lists: Dict[str, List] = {k: list(v) for k, v in lists.items()}",
        "        self.x = 0.0",
        "        self.y = 0.0",
        "        self.direction = 90.0",
        "        self.size = 100.0",
        "        self.rotation_style = \"all around\"",
        "        self.visible = True",
        "        self._stage: Optional[Sprite] = None",
        "        self.hats: List[Dict] = []",
        "        self.costumes: List[Dict] = []",
        "        self._costume_index = 0",
        "        self._costume_md5: str | None = None",
        "        self._ops_prev: int = 0",
        "        self._ops_prev_t: float = 0.0",
        "        self._say_text: str = \"\"",
        "        self._say_type: str = \"\"",
        "        self._effects: Dict[str, float] = {\"color\": 0, \"fisheye\": 0, \"whirl\": 0, \"pixelate\": 0, \"mosaic\": 0, \"brightness\": 0, \"ghost\": 0}",
        "        self.pen_down: bool = False",
        "        self.pen_color = (0, 0, 0, 255)",
        "        self.pen_size = 1",
        "        self.pen_params = {'color': 0.0, 'saturation': 100.0, 'brightness': 50.0, 'transparency': 0.0}",
"        self._px: float = 0.0",
"        self._py: float = 0.0",
"        self._procs: dict = {}",
"        # cache of list-name -> {value: index} so repeated list_index_of",
"        # calls over the same static list do not re-scan O(n) every call",
"        self._list_index_cache: Dict[str, Dict[Any, int]] = {}",
"        self.z_index: int = 0",
"        self._ops: int = 0",
"        self._ops_window: List[float] = []",
"        self._ops_per_sec: float = 0.0",
"        self._spawn_frame: int = 0",
"        self._last_active_frame: int = 0",
"        self._active: bool = False",
"        self._clone: bool = False",
        "        self._dragging: bool = False",
        "        self._sensing_mask_cache = None",
"        self.volume: float = 100.0",
        "    def attach_stage(self, stage: Optional[Sprite]) -> None:",
        "        self._stage = stage",
        "",
        "    def go_to_front_back(self, fb: str) -> None:",
        "        if _eng is None:",
        "            return",
        "        _sprites = _eng.sprites.values()",
        "        if fb == 'front':",
        "            _max_z = max((s.z_index for s in _sprites), default=0)",
        "            self.z_index = _max_z + 1",
        "        else:",
        "            _min_z = min((s.z_index for s in _sprites), default=0)",
        "            self.z_index = _min_z - 1",
        "",
        "    def go_forward_backward_layers(self, fb: str, num: Any) -> None:",
        "        if _eng is None:",
        "            return",
        "        n = int(_num(num))",
        "        if fb == 'backward':",
        "            n = -n",
        "        ordered = sorted(_eng.sprites.values(), key=lambda s: s.z_index)",
        "        try:",
        "            idx = ordered.index(self)",
        "        except ValueError:",
        "            return",
        "        new_idx = max(0, min(len(ordered) - 1, idx + n))",
        "        if new_idx != idx:",
        "            other = ordered[new_idx]",
        "            self.z_index, other.z_index = other.z_index, self.z_index",
        "",
        "    def load_costumes(self, costume_list: List[Dict]) -> None:",
        "        self.costumes = costume_list",
        "        self._costume_name_map = {c.get('name', ''): i for i, c in enumerate(costume_list)}",
        "        self._sensing_mask_cache = None",
        "        if costume_list:",
        '            self._costume_md5 = costume_list[0].get("md5ext")',
        "",
        "    def set_costume(self, name_or_index: Any) -> None:",
        "        name_str = _str(name_or_index)",
        "        idx = self._costume_name_map.get(name_str, -1)",
        "",
        "        if idx == -1:",
        "            for i, c in enumerate(self.costumes):",
        '                if c.get("name") == name_str:',
"                    idx = i",
"                    break",
        "",
        "        if idx == -1:",
        "            try:",
        "                if isinstance(name_or_index, (int, float)):",
        "                    idx = int(name_or_index) - 1",
        "                else:",
        "                    idx = int(float(_num(name_or_index))) - 1",
        "            except (ValueError, TypeError):",
        "                pass",
        "",
        "        if 0 <= idx < len(self.costumes):",
        "            self._costume_index = idx",
        '            self._costume_md5 = self.costumes[idx].get("md5ext")',
        "            self._sensing_mask_cache = None",
        "        elif self.costumes and idx >= 0:",
        "            self._costume_index = idx % len(self.costumes)",
        '            self._costume_md5 = self.costumes[self._costume_index].get("md5ext")',
        "            self._sensing_mask_cache = None",
        "",
        "    def _costume_rotation_center(self):",
        '        """Return (cx, cy) of the current costume rotation center in the',
        '        *native* (unscaled) image pixel space, or image center if absent."""',
        "        if not getattr(self, 'costumes', None) or not (0 <= self._costume_index < len(self.costumes)):",
        "            return None",
        "        _c = self.costumes[self._costume_index]",
        "        _rcx = _c.get('rotationCenterX')",
        "        _rcy = _c.get('rotationCenterY')",
        "        if _rcx is None or _rcy is None:",
        "            return None",
        "        return (float(_rcx), float(_rcy))",
        "",
        "    # ---- variable access  (sp['name'] / sp['name'] = val) ----",
        "",
        "    def __getitem__(self, name: str) -> Any:",
        "        _v = self.vars.get(name)",
        "        if _v is not None or name in self.vars:",
        "            return _v",
        "        _stg = self._stage",
        "        if _stg is not None:",
        "            _v = _stg.vars.get(name)",
        "            if _v is not None or name in _stg.vars:",
        "                return _v",
        "        import sys as _sys_var_warn",
        "        print(f'[WARN] Undefined variable {name!r} in sprite {self.name!r}, returning 0.0', file=_sys_var_warn.stderr)",
        "        return 0.0",
        "",
        "    def __setitem__(self, name: str, value: Any) -> None:",
        "        if name in self.vars:",
        "            self.vars[name] = value",
        "        else:",
        "            _stg = self._stage",
        "            if _stg is not None and name in _stg.vars:",
        "                _stg.vars[name] = value",
        "            else:",
        "                self.vars[name] = value",
        "",
        "    def change_var(self, name: str, by: Any) -> None:",
        "        self[name] = _num(self[name]) + _num(by)",
        "",
        "    # ---- list access ----",
        "",
        "    def get_list(self, name: str) -> List:",
        "        if name in self.lists:",
        "            return self.lists[name]",
        "        if self._stage and name in self._stage.lists:",
        "            return self._stage.lists[name]",
        "        return self.lists.setdefault(name, [])",
        "",
        "    def list_add(self, name: str, item: Any) -> None:",
        "        self.get_list(name).append(item)",
        "        self._invalidate_list_cache(name)",
        "",
        "    def list_delete(self, name: str, idx: Any) -> None:",
        "        lst = self.get_list(name)",
        "        i = _list_index(idx, lst)",
        "        if i is not None and 0 <= i < len(lst):",
        "            del lst[i]",
        "        self._invalidate_list_cache(name)",
        "",
        "    def list_delete_all(self, name: str) -> None:",
        "        self.get_list(name).clear()",
        "        self._invalidate_list_cache(name)",
        "",
        "    def list_replace(self, name: str, idx: Any, item: Any) -> None:",
        "        lst = self.get_list(name)",
        "        i = _list_index(idx, lst)",
        "        if i is not None and 0 <= i < len(lst):",
        "            old = lst[i]",
        "            lst[i] = item",
        "            if type(old) is not type(item):",
        "                self._invalidate_list_cache(name)",
        "",
        "    def list_insert(self, name: str, idx: Any, item: Any) -> None:",
        "        lst = self.get_list(name)",
        "        i = _list_index(idx, lst, allow_append=True)",
        "        if i is None:",
        "            return",
        "        lst.insert(i, item)",
        "        self._invalidate_list_cache(name)",
        "",
        "    def list_item(self, name: str, idx: Any) -> Any:",
        "        lst = self.get_list(name)",
        "        i = _list_index(idx, lst)",
        "        if i is None or not (0 <= i < len(lst)):",
        "            return ''",
        "        return lst[i]",
        "",
        "    def list_length(self, name: str) -> int:",
        "        return len(self.get_list(name))",
        "",
        "    def list_contains(self, name: str, item: Any) -> bool:",
        "        lst = self.get_list(name)",
        "        return any(_eq(x, item) for x in lst)",
        "",
        "    def list_index_of(self, name: str, item: Any) -> int:",
        "        lst = self.get_list(name)",
        "        target = _str(item).lower()",
        "        ",
        "        # Check cache",
        "        cache = self._list_index_cache.get(name)",
        "        if cache is not None and target in cache:",
        "            return cache[target] + 1",
        "        ",
        "        for idx, x in enumerate(lst):",
        "            if _eq(x, item):",
        "                self._list_index_cache.setdefault(name, {})[target] = idx",
        "                return idx + 1",
        "                ",
        "        self._list_index_cache.setdefault(name, {})[target] = -1",
        "        return 0",
        "",
        "    def _invalidate_list_cache(self, name: str) -> None:",
        "        self._list_index_cache.pop(name, None)",
        "",
        "    # ---- procedure call ----",
        "",
        "    def call_proc(self, name: str, args: List) -> None:",
        "        proc = self._procs.get(name)",
        "        if proc:",
        "            gen = proc(self, *args)",
        "            if gen is not None and hasattr(gen, '__next__'):",
        "                if _eng is not None and hasattr(_eng, '_tasks'):",
        "                    _eng._tasks.append(gen)",
        "                else:",
        "                    try:",
        "                        while True: next(gen)",
        "                    except StopIteration:",
        "                        pass",
        "                    except Exception as _e:",
        "                        import sys as _sys",
        "                        print(f'[ENGINE] call_proc {name!r} error: {_e}', file=_sys.stderr)",
        "                        if DEBUG:",
        "                            import traceback",
        "                            traceback.print_exc()",
        "",
        "    def call_proc_gen(self, name: str, args: List) -> Any:",
        "        proc = self._procs.get(name)",
        "        if proc:",
        "            gen = proc(self, *args)",
        "            if gen is not None and hasattr(gen, '__next__'):",
        "                yield from gen",
        "",
        "",
        "# -------------------------------------------------------------------",
        "# Engine  (scheduling, broadcasts)",
        "# -------------------------------------------------------------------",
        "",
        "DEBUG = " + repr(bool(debug)),
        "",
        "class Engine:",
        "    def __init__(self):",
        "        global _eng, _disp",
        "        _eng = self",
        "        self.sprites: Dict[str, Sprite] = {}",
        "        self.stage: Optional[Sprite] = None",
        "        self.running = True",
        "        self._frame = 0",
        "        self._tasks: List = []",
        "        self._task_sprite: Dict[int, str] = {}",
        "        # broadcast-name -> list of (sprite, hat) for O(1) dispatch",
        "        self._broadcast_index: Dict[str, List] = {}",
        "        self._broadcast_dirty: bool = True",
        "        self._timer_start = time.time()",
        '        self._key_states: Dict[str, bool] = {}',
        '        self._mouse = {"x": 0.0, "y": 0.0, "down": False}',
        "        self._display: Any = None",
        "        _disp = None",
        "        self.target_fps = " + repr(float(target_fps)),
        "        # Warp (run-without-screen-refresh) safety: break out if a warp\n"
        "        # loop runs longer than this many seconds (Scratch default ~0.5s).\n"
        "        # Set to 0 to disable the time guard (iteration cap still applies).",
        "        self.warp_time_limit = 0.5  # seconds",
        "        # Fallback iteration cap for warp loops (prevents truly infinite\n"
        "        # hangs when the time guard can't fire fast enough).",
        "        self._warp_iter_limit = 500_000",
        "        # Check time.perf_counter() only every N iterations in warp loops\n"
        "        # to avoid syscall overhead in tight loops (1 = check every iteration).",
        "        self._warp_check_stride = 1000",
        "        self._volume = 100.0",
        "        self._sound_effects: Dict[str, float] = {'pitch': 0.0, 'pan': 0.0}",
        "        self._answer: str = ''",
        "        self._question: str = ''",
        "        self._sounds: Dict[str, Any] = {}",
        "        self._sounds_loaded = False",
        "        self._clone_counter: int = 0",
        "        self._clone_count: int = 0",
        "        self.paused: bool = False",
        "        self._ops_window: deque = deque()",
        "        self._log_entries: deque = deque(maxlen=2000)",
        "        self._log_max: int = 2000",
        "        self._log_lock = None",
        "        self._ops_per_sec: float = 0.0",
        "        self.stats: Dict[str, Any] = {",
        '            "frames": 0, "frame_time": 0.0, "logic_time": 0.0,',
        '            "render_time": 0.0, "fps": 0.0, "ops": 0, "ops_per_sec": 0.0,',
        '            "tasks": 0, "clones": 0, "broadcasts": 0, "paused": False,',
        "        }",
        "",
        "    def log(self, msg: str, level: str = \"INFO\") -> None:",
        "        \"\"\"Append a timestamped entry to the debug log ring buffer.\"\"\"",
        "        try:",
        "            import time as _t",
        "            self._log_entries.append(",
        "                {\"t\": _t.time(), \"frame\": self._frame, \"level\": level, \"msg\": str(msg)})",
        "        except Exception:",
        "            pass",
        "",
        "    def clear_log(self) -> None:",
        "        try:",
        "            self._log_entries.clear()",
        "        except Exception:",
        "            pass",
        "",
        "    @property",
        "    def display(self) -> Any:",
        "        return self._display",
        "",
        "    @display.setter",
        "    def display(self, value: Any) -> None:",
        "        global _disp",
        "        self._display = value",
        "        _disp = value",
        "",
        "    def set_stage(self, sp: Sprite) -> None:",
        "        self.stage = sp",
        "        for s in self.sprites.values():",
        "            s.attach_stage(sp)",
        "",
        "    def create_clone_of(self, target: Any) -> None:",
        '        name = _str(target)',
        "        orig = self.sprites.get(name)",
        "        if orig is None or orig.is_stage:",
        "            return",
        "        # Enforce global 300 clone limit",
        "        current_clones = sum(1 for s in self.sprites.values() if getattr(s, '_clone', False))",
        "        if current_clones >= 300:",
        "            return",
        "        import copy",
        "        cname = name + '_[clone_' + str(self._clone_counter) + ']'",
        "        self._clone_counter += 1",
        "        self._clone_count += 1",
        "        clone = copy.copy(orig)",
        "        clone.name = cname",
        "        # Place the clone on a unique layer just above its original so",
        "        # z-ordering stays stable (Scratch spawns clones above the parent).",
        "        clone.z_index = orig.z_index + self._clone_counter * 1e-6",
        "        clone.vars = dict(orig.vars)",
        "        clone.lists = {k: list(v) for k, v in orig.lists.items()}",
        "        clone._effects = dict(orig._effects)",
        "        clone._sound_effects = dict(orig._sound_effects)",
        "        clone._procs = dict(orig._procs)",
        "        clone._list_index_cache = {}",
        "        clone._px = orig.x",
        "        clone._py = orig.y",
        "        self.sprites[cname] = clone",
        "        if any(h['event'].get('type') == 'event_whenbroadcastreceived' for h in clone.hats):",
        "            clone._has_broadcast_hats = True",
        "            self._broadcast_dirty = True",
        "        else:",
        "            clone._has_broadcast_hats = False",
        '        clone._say_text = ""',
        "        clone._clone = True",
        "        clone._spawn_frame = self._frame",
        "        self.log(f\"clone of {name!r} -> {cname!r}\", \"EVENT\")",
        "        # Fire 'when I start as a clone' hat",
        "        for h in clone.hats:",
        '            if h["event"].get("type") == "control_start_as_clone":',
        '                gen = h["body_gen"](clone)',
        "                if gen is not None and hasattr(gen, '__next__'):",
        "                    self._tasks.append(gen)",
        "                    self._task_sprite[id(gen)] = clone.name",
        "",
        "    def set_input(self, keys: Dict[str, bool],",
        "                  mouse: Dict[str, Any]) -> None:",
        "        self._key_states = keys",
        "        self._mouse = mouse",
        "",
        "    def rebuild_broadcast_index(self) -> None:",
        "        \"\"\"Rebuild the broadcast-name -> [(sprite, hat)] dispatch index.\"\"\"",
        "        idx: Dict[str, List] = {}",
        "        for sp in self.sprites.values():",
        "            for h in sp.hats:",
        "                ev = h[\"event\"]",
        '                if ev.get("type") == "event_whenbroadcastreceived":',
        "                    idx.setdefault(ev.get(\"broadcast\", \"\"), []).append((sp, h))",
        "        self._broadcast_index = idx",
        "        self._broadcast_dirty = False",
        "",
        "    def _dispatch_broadcast(self, name: str) -> None:",
        "        '''Fire broadcast to all registered receivers (non-waiting).'''",
        "        if self._broadcast_dirty:",
        "            self.rebuild_broadcast_index()",
        "        targets = self._broadcast_index.get(name, [])",
        "        for sp, h in targets:",
        '            gen = h["body_gen"](sp)',
        "            if gen is None or not hasattr(gen, '__next__'):",
        "                continue",
        "            try:",
        "                next(gen); self._tasks.append(gen)",
        "                self._task_sprite[id(gen)] = sp.name",
        "                self.stats['ops'] += 1",
        "                sp._ops += 1",
        "                sp._active = True",
        "                sp._last_active_frame = self._frame",
        "            except StopIteration:",
        "                pass",
        "            except Exception as _e:",
        "                if DEBUG:",
        "                    import traceback",
        "                    traceback.print_exc()",
        "",
        "    def _fire_event(self, event_type: str, sprite: object = None) -> None:",
        '        """Fire all hats whose event type matches (sprite click, stage click, ...)."""',
        "        for sp in self.sprites.values():",
        "            if sprite is not None and sp is not sprite:",
        "                continue",
        "            for h in sp.hats:",
        '                if h["event"].get("type") == event_type:',
        "                    try:",
        '                        gen = h["body_gen"](sp)',
        "                        if gen is not None and hasattr(gen, '__next__'):",
        "                            self._tasks.append(gen)",
        "                            self._task_sprite[id(gen)] = sp.name",
        "                            sp._active = True",
        "                            sp._last_active_frame = self._frame",
        "                    except Exception as _e:",
        "                        self.log(f'_fire_event error in {sp.name!r}: {_e}', 'ERROR')",
        "                        if DEBUG:",
        "                            import traceback",
        "                            traceback.print_exc()",
        "",
        "    def broadcast(self, name: str) -> None:",
        "        self.stats['broadcasts'] = self.stats.get('broadcasts', 0) + 1",
        "        self.log(f\"broadcast {name!r}\", \"EVENT\")",
        "        if DEBUG:",
        "            print(f\"[ENGINE] broadcast {name!r}\", flush=True)",
        "        self._dispatch_broadcast(name)",
        "",
        "    def broadcast_and_wait(self, name: str):",
        "        '''Broadcast and wait for receivers to complete.'''",
        "        self.stats['broadcasts'] = self.stats.get('broadcasts', 0) + 1",
        "        self.log(f\"broadcast (and wait) {name!r}\", \"EVENT\")",
        "        if DEBUG:",
        "            print(f\"[ENGINE] broadcast_and_wait {name!r}\", flush=True)",
        "        if self._broadcast_dirty:",
        "            self.rebuild_broadcast_index()",
        "        targets = self._broadcast_index.get(name, [])",
        "        if not targets:",
        "            return",
        "        # Run each receiver as a task, yielding until all complete",
        "        _recv_gens = []",
        "        for sp, h in targets:",
        "            gen = h[\"body_gen\"](sp)",
        "            if gen is not None and hasattr(gen, '__next__'):",
        "                try:",
        "                    next(gen)",
        "                    self.stats['ops'] += 1",
        "                    sp._ops += 1",
        "                    sp._active = True",
        "                    sp._last_active_frame = self._frame",
        "                    _recv_gens.append((gen, sp))",
        "                except StopIteration:",
        "                    pass",
        "                except Exception:",
        "                    if DEBUG:",
        "                        import traceback",
        "                        traceback.print_exc()",
        "        # Advance receivers cooperatively, one step per yield",
        "        while _recv_gens:",
        "            _still_alive = []",
        "            for gen, sp in _recv_gens:",
        "                try:",
        "                    next(gen)",
        "                    self.stats['ops'] += 1",
        "                    sp._ops += 1",
        "                    sp._active = True",
        "                    sp._last_active_frame = self._frame",
        "                    _still_alive.append((gen, sp))",
        "                except StopIteration:",
        "                    pass",
        "                except Exception:",
        "                    if DEBUG:",
        "                        import traceback",
        "                        traceback.print_exc()",
        "            _recv_gens = _still_alive",
        "            if _recv_gens:",
        "                yield",
        "",
        "    def load_sounds(self, sound_data: Dict[str, List[Dict]]) -> None:",
        "        # Map sound name -> asset path for every target",
        "        if self.display is not None and getattr(self.display, 'assets_dir', None) is not None:",
        "            base = Path(self.display.assets_dir) / 'data'",
        "        else:",
        "            base = Path('data')",
        "        for sn, sdl in sound_data.items():",
        "            for c in sdl:",
        "                name = c.get('name')",
        "                md5ext = c.get('md5ext', '')",
        "                if not name or not md5ext:",
        "                    continue",
        "                path = base / sn / md5ext",
        "                if path.exists():",
        "                    self._sounds[name] = str(path)",
        "        self._sounds_loaded = True",

        "    _wav_cache: Dict[str, Any] = {}  # PATCH 1: WAV decode cache",
        "",
        "    def _play_wav_adjusted(self, path: str, sync: bool, volume01: float, pitch_semitones: float) -> bool:",
        "        '''Best-effort playback with real volume + pitch control via numpy +",
        "        sounddevice. Returns True if it handled playback, False otherwise so",
        "        the caller can fall back to a platform player.'''",
        "        try:",
        "            import numpy as _npa, wave as _wave, sounddevice as _sd",
        "        except Exception:",
        "            return False",
        "        try:",
        "            _path_str = str(path)",
        "            if _path_str not in Sprite._wav_cache:",
        "                with _wave.open(_path_str, 'rb') as _w:",
        "                    _ch = _w.getnchannels()",
        "                    _sw = _w.getsampwidth()",
        "                    _fr = _w.getframerate()",
        "                    _raw = _w.readframes(_w.getnframes())",
        "                _dtype = {1: _npa.int8, 2: _npa.int16, 4: _npa.int32}.get(_sw)",
        "                if _dtype is None:",
        "                    return False",
        "                _data = _npa.frombuffer(_raw, dtype=_dtype).astype(_npa.float32)",
        "                _maxv = float(2 ** (8 * _sw - 1))",
        "                Sprite._wav_cache[_path_str] = (_data / _maxv, _ch, _fr)",
        "            _data, _ch, _fr = Sprite._wav_cache[_path_str]",
        "            if _ch > 1:",
        "                _data = _data.reshape(-1, _ch)",
        "            _play = _data.copy()",
        "            # Volume",
        "            _play = _play * max(0.0, min(1.0, volume01))",
        "            # Pitch: resample by playing at an adjusted sample rate.",
        "            _rate = int(_fr * (2.0 ** (pitch_semitones / 12.0)))",
        "            _rate = max(1, _rate)",
        "            _sd.play(_play, _rate)",
        "            if sync:",
        "                _sd.wait()",
        "            return True",
        "        except Exception:",
        "            return False",
"",
"    def _play_wav(self, path: str, sync: bool = False, volume: float = 100.0) -> None:",
"        _pitch = self._sound_effects.get('pitch', 0.0)  # semitones",
"        _pan = self._sound_effects.get('pan', 0.0)      # -100..100",
"        _vol = max(0.0, min(100.0, volume)) / 100.0",
"        # Preferred path: real volume + pitch control (all platforms, incl. Windows).",
"        if self._play_wav_adjusted(str(path), sync, _vol, _pitch):",
"            return",
"        try:",
"            import winsound",
"            flags = winsound.SND_FILENAME",
"            if not sync:",
"                flags |= winsound.SND_ASYNC",
"            # winsound has no volume/pitch control; used only as a last resort",
"            # after the numpy+sounddevice path above is unavailable.",
"            winsound.PlaySound(str(path), flags)",
"        except ImportError:",
"            try:",
"                import subprocess, platform",
"                s = platform.system()",
"                if s == 'Darwin':",
"                    # afplay supports volume via -v (0.0 to 1.0)",
"                    _rate = 2.0 ** (_pitch / 12.0)",
"                    subprocess.Popen(['afplay', '-r', f'{_rate:.4f}', '-v', f'{_vol:.3f}', str(path)],",
"                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
"                elif s == 'Linux':",
"                    # play (sox) supports volume via -v",
"                    _rate = 2.0 ** (_pitch / 12.0)",
"                    subprocess.Popen(['play', '-q', '-V0', '-v', f'{_vol:.3f}', str(path), 'pitch', f'{_pitch:.1f}'],",
"                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
"                else:",
"                    # aplay doesn't support volume easily; play at default",
"                    subprocess.Popen(['aplay', str(path)],",
"                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
"            except Exception:",
"                pass",
"        except Exception:",
"            pass",

"    def play_sound(self, name: Any, volume: float = None) -> None:",
"        path = self._sounds.get(_str(name))",
"        if not path:",
"            return",
"        if volume is None:",
"            volume = self._volume",
"        self._play_wav(str(path), sync=False, volume=volume)",
"",
        "    _wav_dur_cache: Dict[str, float] = {}  # PATCH 2: WAV duration cache",
        "",
        "    def _wav_duration(self, path: str) -> float:",
        "        _p = str(path)",
        "        if _p not in Sprite._wav_dur_cache:",
        "            try:",
        "                import wave",
        "                with wave.open(_p, 'rb') as _w:",
        "                    _frames = _w.getnframes()",
        "                    _rate = _w.getframerate() or 1",
        "                    Sprite._wav_dur_cache[_p] = _frames / float(_rate)",
        "            except Exception:",
        "                Sprite._wav_dur_cache[_p] = 0.0",
        "        return Sprite._wav_dur_cache[_p]",
"",
"    def play_sound_until_done(self, name: Any, volume: float = None):",
"        path = self._sounds.get(_str(name))",
"        if not path:",
"            return",
"        if volume is None:",
"            volume = self._volume",
"        self._play_wav(str(path), sync=False, volume=volume)",
"        # Block this script (but not the whole engine) until playback ends,",
"        # matching Scratch's 'play sound until done'. Other tasks keep running",
"        # because we cooperatively yield each frame until the duration elapses.",
"        _dur = self._wav_duration(str(path))",
"        _end = time.time() + _dur",
"        while time.time() < _end:",
"            yield",

        "    def stop_sounds(self) -> None:",
        "        try:",
        "            import sounddevice as _sd_stop",
        "            _sd_stop.stop()",
        "        except Exception:",
        "            pass",
        "        try:",
        "            import winsound",
        "            winsound.PlaySound(None, winsound.SND_PURGE)",
        "        except Exception:",
        "            pass",

        "    def set_volume(self, v: Any) -> None:",
        "        self._volume = max(0.0, min(100.0, _num(v)))",

        "    def change_volume(self, v: Any) -> None:",
        "        self._volume = max(0.0, min(100.0, self._volume + _num(v)))",

        "    def get_volume(self) -> float:",
        "        return self._volume",
        "",
        "    def ask_and_wait(self, question: str):",
        "        '''Non-blocking ask. Returns a generator that yields until the",
        "        user submits an answer, then returns the answer string. The",
        "        engine keeps running (other sprites/animations continue).'''",
        "        if self._display is not None:",
        "            return self._display.ask_async(question)",
        "        return iter([''])",
        "",
        "    def start(self, green_flag: bool = True) -> None:",
        "        self._broadcast_dirty = True",
        "        _clone_names = [n for n, s in self.sprites.items() if s._clone]",
        "        for cn in _clone_names:",
        "            self.sprites.pop(cn, None)",
        "        self._clone_count = 0",
        "        self._tasks = []",
        "        self._task_sprite = {}",
        "        self.running = True",
        "        self.paused = False",
        "        if self._display is not None:",
        "            self._display.hide_ask()",
        "        self._timer_start = time.time()",
        "        self.stats['broadcasts'] = 0",
        "        for sp in self.sprites.values():",
        "            sp._ops = 0",
        "            sp._ops_per_sec = 0.0",
        "            sp._ops_window = []",
        "            sp._spawn_frame = self._frame",
        "            sp._active = False",
        "            sp._last_active_frame = self._frame",
        "            sp._clone = '_[clone_' in sp.name",
        "        if green_flag:",
        "            self.log(\"GREEN FLAG clicked\", \"CONTROL\")",
        "        for sp in self.sprites.values():",
        "            for h in sp.hats:",
        "                ev = h[\"event\"]",
        '                if green_flag and ev.get("type") == "event_whenflagclicked":',
        '                    gen = h["body_gen"](sp)',
        "                    if gen is not None:",
        "                        self._tasks.append(gen)",
        "                        self._task_sprite[id(gen)] = sp.name",
        "                        sp._active = True",
        "                        sp._last_active_frame = self._frame",
        "",
        "    def run_frame(self) -> None:",
        "        self._frame += 1",
        "        if DEBUG and self._frame % 30 == 0:",
        "            print(f\"[ENGINE] frame {self._frame} tasks={len(self._tasks)}\", flush=True)",
        "        self.stats['paused'] = self.paused",
        "        if self.paused:",
        "            self.stats['tasks'] = len(self._tasks)",
        "            self.stats['clones'] = self._clone_count",
        "            self.stats[\"frames\"] = self._frame",
        "            return",
        "        tasks = self._tasks",
        "        self._tasks = []",
        "        delete_clones = []",
        "        stop_other_scripts = []",
        "        for g in tasks:",
        "            sp_name = self._task_sprite.get(id(g))",
        "            sp = self.sprites.get(sp_name) if sp_name else None",
        "            try:",
        "                result = next(g)",
        "                self._tasks.append(g)",
        "                self.stats['ops'] += 1",
        "                if sp is not None:",
        "                    sp._ops += 1",
        "                    sp._active = True",
        "                    sp._last_active_frame = self._frame",
        "                if result == '__DELETE_CLONE__':",
        "                    sp_name = self._task_sprite.pop(id(g), None)",
        "                    if sp_name and '_[clone_' in sp_name:",
        "                        delete_clones.append(sp_name)",
        "            except StopIteration as _si:",
        "                _val = getattr(_si, 'value', None)",
        "                if _val == '__STOP_ALL__':",
        "                    self.running = False",
        "                    self._stop_all_requested = True",
        "                elif _val == '__DELETE_CLONE__':",
        "                    sp_name = self._task_sprite.pop(id(g), None)",
        "                    if sp_name and '_[clone_' in sp_name:",
        "                        delete_clones.append(sp_name)",
        "                elif _val == '__STOP_OTHER__':",
        "                    _sname = self._task_sprite.get(id(g))",
        "                    if _sname:",
        "                        stop_other_scripts.append(_sname)",
        "            except Exception as _e:",
        "                self.log(f'Task error: {_e}', 'ERROR')",
        "                if DEBUG:",
        "                    import traceback",
        "                    traceback.print_exc()",
        "        for cn in delete_clones:",
        "            _dsp = self.sprites.pop(cn, None)",
        "            self._clone_count = max(0, self._clone_count - 1)",
        "            if _dsp and getattr(_dsp, '_has_broadcast_hats', False):",
        "                self._broadcast_dirty = True",
        "        # Immediately terminate all orphaned clone threads",
        "        if delete_clones:",
        "            self._tasks = [g for g in self._tasks if self._task_sprite.get(id(g)) not in delete_clones]",
        "            # Clean up the task-to-sprite index",
        "            for tid in list(self._task_sprite.keys()):",
        "                if self._task_sprite[tid] in delete_clones:",
        "                    self._task_sprite.pop(tid, None)",
        "        # 'stop other scripts in sprite': kill sibling tasks on same sprite",
        "        if stop_other_scripts:",
        "            self._tasks = [g for g in self._tasks if self._task_sprite.get(id(g)) not in stop_other_scripts]",
        "            for tid in list(self._task_sprite.keys()):",
        "                if self._task_sprite[tid] in stop_other_scripts:",
        "                    self._task_sprite.pop(tid, None)",
        "        # ---- per-sprite + global ops/sec (rolling 1s window) ----",
        "        try:",
        "            import time as _t",
        "            _now = _t.time()",
        "            self._ops_window.append(_now)",
        "            while self._ops_window and _now - self._ops_window[0] > 1.0:",
        "                self._ops_window.popleft()",
        "            _dt = max(1e-6, _now - self._ops_window[0]) if self._ops_window else 1.0",
        "            total_ops = self.stats.get('ops', 0)",
        "            self._ops_per_sec = len(self._ops_window) / _dt",
        "            self.stats['ops_per_sec'] = self._ops_per_sec",
        "            for sp in self.sprites.values():",
        "                _prev_ops = getattr(sp, '_ops_prev', None)",
        "                _prev_t = getattr(sp, '_ops_prev_t', None)",
        "                if _prev_ops is not None and _prev_t is not None:",
        "                    _dops = sp._ops - _prev_ops",
        "                    _dt2 = max(1e-6, _now - _prev_t)",
        "                    sp._ops_per_sec = _dops / _dt2",
        "                sp._ops_prev = sp._ops",
        "                sp._ops_prev_t = _now",
        "                sp._active = sp._active and (self._frame - sp._last_active_frame) < 30",
        "        except Exception:",
        "            pass",
        "        self.stats['tasks'] = len(self._tasks)",
        "        self.stats['clones'] = self._clone_count",
        "        self.stats[\"frames\"] = self._frame",
        "",
        "",
        "# ===================================================================",
        "#  Shared helper functions (used by decompiled sprite modules)",
        "# ===================================================================",
        "",
        "",
        "def _sensing_current(unit: str) -> int:",
        "    import datetime as _dt",
        "    _now = _dt.datetime.now()",
        "    _m = {'YEAR': _now.year, 'MONTH': _now.month, 'DATE': _now.day,",
        "          'DAYOFWEEK': _now.isoweekday() % 7 + 1, 'HOUR': _now.hour,",
        "          'MINUTE': _now.minute, 'SECOND': _now.second}",
        "    return _m[unit]",
        "",
        "",
        "def _sensing_days2000() -> float:",
        "    import datetime as _dt",
        "    # Local-timezone aware: respects the user's timezone offset.",
        "    _diff = _dt.datetime.now() - _dt.datetime(2000, 1, 1)",
        "    return _diff.days + (_diff.seconds + _diff.microseconds / 1e6) / 86400.0",
        "",
        "",
        "def _sensing_loudness() -> float:",
        '    """Return current microphone level (0-100) from a background buffer.',
        '    Starts a streaming reader on first call so it never blocks."""',
        '    if not hasattr(_sensing_loudness, "_buffer"):',
        "        _sensing_loudness._buffer = deque(maxlen=64)",
        "        _sensing_loudness._lock = threading.Lock()",
        "        _sensing_loudness._running = True",
        "        def _reader():",
        "            try:",
        "                import numpy as _np",
        "                import sounddevice as _sd",
        "                _sr = 22050",
        "                def _cb(indata, *_a):",
        "                    try:",
        "                        with _sensing_loudness._lock:",
        "                            _sensing_loudness._buffer.append(_np.sqrt(_np.mean(indata**2)) * 100.0)",
        "                    except Exception:",
        "                        pass",
        "                with _sd.InputStream(samplerate=_sr, channels=1, callback=_cb):",
        "                    while _sensing_loudness._running:",
        "                        time.sleep(0.05)",
        "            except Exception:",
        "                pass",
        "        threading.Thread(target=_reader, daemon=True).start()",
        "    try:",
        "        with _sensing_loudness._lock:",
        "            if _sensing_loudness._buffer:",
        "                return max(0.0, min(100.0, _sensing_loudness._buffer.popleft()))",
        "    except Exception:",
        "        pass",
        "    return 0.0",
        "",
        "",
        "def _sensing_of(sp: Sprite, obj: Any, prop: str) -> Any:",
        "    _tgt = _eng.sprites.get(_str(obj), _eng.stage)",
        "    if _tgt is None:",
        "        return ''",
        "    _pm = {",
        "        'x position': _tgt.x, 'x': _tgt.x,",
        "        'y position': _tgt.y, 'y': _tgt.y,",
        "        'direction': _tgt.direction,",
        "        'size': _tgt.size,",
        "        'volume': getattr(_eng, \"_volume\", 100.0) if _tgt.is_stage else 100.0,",
        "        'costume number': _tgt._costume_index + 1,",
        "        'costume_number': _tgt._costume_index + 1,",
        "        'costume name': _tgt.costumes[_tgt._costume_index].get('name', '') if _tgt.costumes else '',",
        "        'costume_name': _tgt.costumes[_tgt._costume_index].get('name', '') if _tgt.costumes else '',",
        "        'backdrop number': _eng.stage._costume_index + 1 if _eng.stage else 1,",
        "        'backdrop_number': _eng.stage._costume_index + 1 if _eng.stage else 1,",
        "        'backdrop name': _eng.stage.costumes[_eng.stage._costume_index].get('name', '') if _eng.stage and _eng.stage.costumes else '',",
        "        'backdrop_name': _eng.stage.costumes[_eng.stage._costume_index].get('name', '') if _eng.stage and _eng.stage.costumes else '',",
        "    }",
        "    if prop in _pm:",
        "        return _pm[prop]",
        "    if prop in _tgt.vars:",
        "        return _tgt.vars[prop]",
        "    # Lists from another target: Scratch returns items joined. If every",
        "    # item is a single character it joins with '', otherwise with ' '.",
        "    if prop in getattr(_tgt, 'lists', {}):",
        "        _lst = _tgt.lists[prop]",
        "        _parts = [_str(_it) for _it in _lst]",
        "        if all(len(_p) == 1 for _p in _parts):",
        "            return ''.join(_parts)",
        "        return ' '.join(_parts)",
         "    return ''",
        "",
        "",
        "def _get_sprite_mask(sp, disp):",
        "    \"\"\"Get (alpha_mask, nw, nh) for a sprite's current costume, cached per costume/size/direction.\"\"\"",
        "    _md5 = sp._costume_md5",
        "    if not _md5:",
        "        return None",
        "    _img = disp.load_costume(_md5, getattr(sp, 'name', None))",
        "    if _img is None:",
        "        return None",
        "    _s = max(0.0, sp.size) / 100.0",
        "    _w, _h = _img.size",
        "    _nw = max(1, int(_w * _s))",
        "    _nh = max(1, int(_h * _s))",
        "    _cache_key = (_md5, _nw, _nh, sp.direction, getattr(sp, 'rotation_style', 'all around'))",
        "    cached = getattr(sp, '_sensing_mask_cache', None)",
        "    if cached is not None and cached[0] == _cache_key:",
        "        return cached[1]",
        "    _sc = disp.get_scaled(_md5, _nw, _nh, _img)",
        "    if _sc is not None:",
        "        _img = _sc",
        "    _img = _transform_costume(_img, sp)",
        "    try:",
        "        import numpy as _np",
        "        _arr = _np.asarray(_img)",
        "        _amask = _arr[:, :, 3] > 10",
        "        result = (_amask, _nw, _nh)",
        "        sp._sensing_mask_cache = (_cache_key, result)",
        "        return result",
        "    except Exception:",
        "        return None",
        "",
        "",
        "def _sensing_distanceto(sp: Sprite, subj: Any) -> float:",
        "    if subj == '_mouse_':",
        "        _tx, _ty = _eng._mouse['x'], _eng._mouse['y']",
        "    elif subj == '_edge_':",
        "        return 100.0",
        "    else:",
        "        _gs = _eng.sprites.get(_str(subj))",
        "        if _gs is None: return 100.0",
        "        _tx, _ty = _gs.x, _gs.y",
        "    return ((sp.x - _tx)**2 + (sp.y - _ty)**2) ** 0.5",
        "",
        "",
        "def _sensing_touchingobject(sp: Sprite, obj: Any) -> bool:",
        "    '''Check if sprite is touching another sprite, mouse, or edge.'''",
        "    if obj == '_mouse_':",
        "        if _disp is None:",
        "            return False",
        "        _md5 = sp._costume_md5",
        "        if not _md5:",
        "            return False",
        "        _img = _disp.load_costume(_md5)",
        "        if _img is None:",
        "            return False",
        "        _s = max(0.0, sp.size) / 100.0",
        "        _w, _h = _img.size",
        "        _nw = max(1, int(_w * _s))",
        "        _nh = max(1, int(_h * _s))",
        "        _mx = _eng._mouse.get('x', 0)",
        "        _my = _eng._mouse.get('y', 0)",
        "        if _mx is None or _my is None:",
        "            return False",
        "        _rx = sp.x - _nw / (2.0 * _disp.scale)",
        "        _ry = sp.y - _nh / (2.0 * _disp.scale)",
        "        _rw = _nw / _disp.scale",
        "        _rh = _nh / _disp.scale",
        "        return _rx <= _mx <= _rx + _rw and _ry <= _my <= _ry + _rh",
        "    if obj == '_edge_':",
        "        _w2 = 240; _h2 = 180",
        "        _s = max(0.0, sp.size) / 100.0",
        "        _md5 = sp._costume_md5",
        "        if _md5 and _disp:",
        "            _img = _disp.load_costume(_md5)",
        "            if _img:",
        "                _nw = _img.size[0] * _s / _disp.scale",
        "                _nh = _img.size[1] * _s / _disp.scale",
        "                if sp.x - _nw/2 < -_w2 or sp.x + _nw/2 > _w2:",
        "                    return True",
        "                if sp.y - _nh/2 < -_h2 or sp.y + _nh/2 > _h2:",
        "                    return True",
        "                return False",
        "        return sp.x < -_w2 or sp.x > _w2 or sp.y < -_h2 or sp.y > _h2",
        "    _tgt = _eng.sprites.get(_str(obj))",
        "    if _tgt is None:",
        "        return False",
        "    if _disp is None:",
        "        return False",
        "    _md5_a = sp._costume_md5",
        "    _md5_b = _tgt._costume_md5",
        "    if not _md5_a or not _md5_b:",
        "        return False",
        "    _img_a = _disp.load_costume(_md5_a)",
        "    _img_b = _disp.load_costume(_md5_b)",
        "    if _img_a is None or _img_b is None:",
        "        return False",
        "    _sa = max(0.0, sp.size) / 100.0",
        "    _sb = max(0.0, _tgt.size) / 100.0",
        "    _wa, _ha = _img_a.size",
        "    _wb, _hb = _img_b.size",
        "    _nwa, _nha = max(1, int(_wa * _sa)), max(1, int(_ha * _sa))",
        "    _nwb, _nhb = max(1, int(_wb * _sb)), max(1, int(_hb * _sb))",
        "    _ax = sp.x - _nwa / (2.0 * _disp.scale)",
        "    _ay = sp.y - _nha / (2.0 * _disp.scale)",
        "    _bx = _tgt.x - _nwb / (2.0 * _disp.scale)",
        "    _by = _tgt.y - _nhb / (2.0 * _disp.scale)",
        "    _ar = _nwa / _disp.scale",
        "    _ah = _nha / _disp.scale",
        "    _br = _nwb / _disp.scale",
        "    _bh = _nhb / _disp.scale",
        "    # Quick fail: bounding boxes do not overlap",
        "    if (_ax + _ar < _bx or _bx + _br < _ax or _ay + _ah < _by or _by + _bh < _ay):",
        "        return False",
        "    # Pixel-perfect check over the overlapping region (rotated/flip-aware)",
        "    try:",
        "        from PIL import Image as _PIL",
        "        _PIL_LANCZOS = _PIL.Resampling.LANCZOS",
        "        if _img_a.size != (_nwa, _nha): _img_a = _img_a.resize((_nwa, _nha), _PIL_LANCZOS)",
        "        if _img_b.size != (_nwb, _nhb): _img_b = _img_b.resize((_nwb, _nhb), _PIL_LANCZOS)",
        "        # Apply visual transforms (rotation / left-right flip) to hitboxes",
        "        _rs_a = getattr(sp, 'rotation_style', 'all around')",
        "        _dir_a = getattr(sp, 'direction', 90.0)",
        "        if _rs_a == 'all around' and _dir_a != 90.0:",
        "            _img_a = _img_a.rotate(90.0 - _dir_a, expand=True, resample=_PIL_ROTRES)",
        "        elif _rs_a == 'left-right' and (90.0 < (_dir_a % 360.0) < 270.0):",
        "            _img_a = _img_a.transpose(_PIL.Image.FLIP_LEFT_RIGHT)",
        "        _rs_b = getattr(_tgt, 'rotation_style', 'all around')",
        "        _dir_b = getattr(_tgt, 'direction', 90.0)",
        "        if _rs_b == 'all around' and _dir_b != 90.0:",
        "            _img_b = _img_b.rotate(90.0 - _dir_b, expand=True, resample=_PIL_ROTRES)",
        "        elif _rs_b == 'left-right' and (90.0 < (_dir_b % 360.0) < 270.0):",
        "            _img_b = _img_b.transpose(_PIL.Image.FLIP_LEFT_RIGHT)",
        "        import numpy as _np",
        "        _arr_a = _np.asarray(_img_a)",
        "        _arr_b = _np.asarray(_img_b)",
        "        _amask = _arr_a[:, :, 3] > 10",
        "        _bmask = _arr_b[:, :, 3] > 10",
        "        if not (_amask.any() and _bmask.any()):",
        "            return False",
        "        _ays, _axs = _np.nonzero(_amask)",
        "        _bxs_all, _bys_all = _np.nonzero(_bmask)",
        "        # Map sprite-A solid pixels into sprite-B's local image coordinates",
        "        _scale = _disp.scale",
        "        _agx = _ax + _axs / _scale",
        "        _agy = _ay + _ays / _scale",
        "        _blx = ((_agx - _bx) * _scale).astype(_np.int64)",
        "        _bly = ((_agy - _by) * _scale).astype(_np.int64)",
        "        _ok = (0 <= _blx) & (_blx < _arr_b.shape[1]) & (0 <= _bly) & (_bly < _arr_b.shape[0])",
        "        _blx = _blx[_ok]; _bly = _bly[_ok]",
        "        if _blx.size == 0:",
        "            return False",
        "        _bm = _bmask[_bly, _blx]",
        "        return bool(_bm.any())",
        "    except Exception:",
        "        pass",
        "    return False",
        "",
        "",
        "def _transform_costume(img, sp):",
        '    """Apply rotation / left-right flip to a costume image per sp rotation state."""',
        "    try:",
        "        from PIL import Image as _PIL",
        "        _rs = getattr(sp, 'rotation_style', 'all around')",
        "        _dir = getattr(sp, 'direction', 90.0)",
        "        if _rs == 'all around' and _dir != 90.0:",
        "            img = img.rotate(90.0 - _dir, expand=True, resample=_PIL.Resampling.LANCZOS)",
        "        elif _rs == 'left-right' and (90.0 < (_dir % 360.0) < 270.0):",
        "            img = img.transpose(_PIL.Image.FLIP_LEFT_RIGHT)",
        "    except Exception:",
        "        pass",
        "    return img",
        "",
        "",
        "def _sensing_touchingcolor(sp: Sprite, color: Any) -> bool:",
        "    if _disp is None:",
        "        return False",
        "    # PATCH 23: per-frame memoization cache",
        "    _cache_key = (id(sp), int(color) if isinstance(color, (int, float)) else str(color), _disp._frame if hasattr(_disp, '_frame') else -1)",
        "    if not hasattr(_sensing_touchingcolor, '_cache'):",
        "        _sensing_touchingcolor._cache = {}",
        "    if _cache_key in _sensing_touchingcolor._cache:",
        "        return _sensing_touchingcolor._cache[_cache_key]",
        "    _md5 = sp._costume_md5",
        "    if not _md5:",
        "        return False",
        "    _img = _disp.load_costume(_md5)",
        "    if _img is None:",
        "        return False",
        "    _s = max(0.0, sp.size) / 100.0",
        "    _w, _h = _img.size",
        "    _nw = max(1, int(_w * _s))",
        "    _nh = max(1, int(_h * _s))",
        "    _sc = _disp.get_scaled(_md5, _nw, _nh, _img)",
        "    if _sc is not None:",
        "        _img = _sc",
        "    _img = _transform_costume(_img, sp)",
        "    try:",
        "        _cv = int(color)",
        "    except (ValueError, TypeError):",
        "        return False",
        "    _rt = (_cv >> 16) & 0xFF",
        "    _gt = (_cv >> 8) & 0xFF",
        "    _bt = _cv & 0xFF",
        "    _sx = _disp.scale",
        "    _px = int((sp.x + _disp.stage_w / 2) * _sx - _nw / 2)",
        "    _py = int((_disp.stage_h / 2 - sp.y) * _sx - _nh / 2)",
        "    try:",
        "        import numpy as _np",
        "        _stage_img = _disp._last_frame if getattr(_disp, '_last_frame', None) is not None else _np.asarray(_disp.stage)",
        "        _arr = _np.asarray(_img)",
        "        _alpha = _arr[:, :, 3] > 0",
"        if not _alpha.any():",
        "            _sensing_touchingcolor._cache[_cache_key] = False",
        "            return False",
        "        _ys, _xs = _np.nonzero(_alpha)",
        "        _gxs = _px + _xs",
        "        _gys = _py + _ys",
        "        _mask = (0 <= _gxs) & (_gxs < _stage_img.shape[1]) & (0 <= _gys) & (_gys < _stage_img.shape[0])",
        "        _gxs = _gxs[_mask]; _gys = _gys[_mask]",
        "        if _gxs.size == 0:",
        "            _sensing_touchingcolor._cache[_cache_key] = False",
        "            return False",
        "        _samp = _stage_img[_gys, _gxs, :3]",
        "        _result = bool(_np.logical_and.reduce((_samp[:, 0] == _rt, _samp[:, 1] == _gt, _samp[:, 2] == _bt)).any())",
        "        _sensing_touchingcolor._cache[_cache_key] = _result",
        "        return _result",
        "    except Exception:",
        "        _sensing_touchingcolor._cache[_cache_key] = False",
        "        return False",
        "",
        "",
        "def _sensing_coloristouchingcolor(sp: Sprite, color: Any, color2: Any) -> bool:",
        "    if _disp is None:",
        "        return False",
        "    _md5 = sp._costume_md5",
        "    if not _md5:",
        "        return False",
        "    _img = _disp.load_costume(_md5)",
        "    if _img is None:",
        "        return False",
        "    _s = max(0.0, sp.size) / 100.0",
        "    _w, _h = _img.size",
        "    _nw = max(1, int(_w * _s))",
        "    _nh = max(1, int(_h * _s))",
        "    _sc = _disp.get_scaled(_md5, _nw, _nh, _img)",
        "    if _sc is not None:",
        "        _img = _sc",
        "    _img = _transform_costume(_img, sp)",
        "    try:",
        "        _cv1, _cv2 = int(color), int(color2)",
        "    except (ValueError, TypeError):",
        "        return False",
        "    _r1 = (_cv1 >> 16) & 0xFF; _g1 = (_cv1 >> 8) & 0xFF; _b1 = _cv1 & 0xFF",
        "    _r2 = (_cv2 >> 16) & 0xFF; _g2 = (_cv2 >> 8) & 0xFF; _b2 = _cv2 & 0xFF",
        "    _sx = _disp.scale",
        "    _px = int((sp.x + _disp.stage_w / 2) * _sx - _nw / 2)",
        "    _py = int((_disp.stage_h / 2 - sp.y) * _sx - _nh / 2)",
"    try:",
        "        import numpy as _np",
        "        _stage_img = _disp._last_frame if getattr(_disp, '_last_frame', None) is not None else _np.asarray(_disp.stage)",
        "        _arr = _np.asarray(_img)",
        "        _mask1 = (_arr[:, :, 0] == _r1) & (_arr[:, :, 1] == _g1) & (_arr[:, :, 2] == _b1) & (_arr[:, :, 3] > 0)",
        "        if not _mask1.any():",
        "            return False",
        "        _ys, _xs = _np.nonzero(_mask1)",
        "        # 4-neighbour offsets on the costume grid",
        "        _off = _np.array([(0,0),(1,0),(-1,0),(0,1),(0,-1)], dtype=_xs.dtype)",
        "        _pts = _np.stack([_xs, _ys], axis=1)[:, None, :] + _off[None, :, :]",
        "        _pxs = _px + _pts[:, :, 0].ravel()",
        "        _pys = _py + _pts[:, :, 1].ravel()",
        "        _b = (0 <= _pxs) & (_pxs < _stage_img.shape[1]) & (0 <= _pys) & (_pys < _stage_img.shape[0])",
        "        _pxs = _pxs[_b]; _pys = _pys[_b]",
        "        if _pxs.size == 0:",
        "            return False",
        "        _samp = _stage_img[_pys, _pxs, :3]",
        "        return bool(_np.logical_and.reduce((_samp[:, 0] == _r2, _samp[:, 1] == _g2, _samp[:, 2] == _b2)).any())",
        "    except Exception:",
        "        return False",
        "",
        "",
        "def _display_clear():",
        "    if _disp: _disp.clear()",
        "",
        "def _display_stamp(sp, x: float, y: float, size: float):",
        "    if not _disp or not sp: return",
        "    md5 = getattr(sp, '_costume_md5', None)",
        "    if not md5:",
        "        costumes = getattr(sp, 'costumes', None)",
        "        idx = getattr(sp, '_costume_index', 0)",
        "        if costumes and 0 <= idx < len(costumes):",
        "            md5 = costumes[idx].get('md5ext')",
        "    if md5:",
        "        _disp.stamp(md5, x, y, size, getattr(sp, 'name', None) if sp is not None else None)",
        "    elif DEBUG:",
        "        print('[stamp] no costume md5 for sprite %r' % (getattr(sp, 'name', sp),))",
        "",
        "def _display_penup(sp):",
        "    if sp: sp.pen_down = False",
        "",
        "def _display_pendown(sp):",
        "    if sp:",
        "        sp.pen_down = True",
        "        if _disp:",
        "            _disp.pen_color = sp.pen_color",
        "            _disp.pen_size = sp.pen_size",
        "            _disp.draw_point(sp, sp.x, sp.y)",
        "",
        "def _hsbt_to_rgba(h, s, b, t):",
        "    h = (h % 100.0) / 100.0",
        "    s = max(0, min(100, s)) / 100.0",
        "    b = max(0, min(100, b)) / 100.0",
        "    t = max(0, min(100, t)) / 100.0",
        "    c = b * s",
        "    hp = h * 6.0",
        "    x = c * (1.0 - abs(hp % 2.0 - 1.0))",
        "    if hp < 1.0: r1, g1, bl1 = c, x, 0.0",
        "    elif hp < 2.0: r1, g1, bl1 = x, c, 0.0",
        "    elif hp < 3.0: r1, g1, bl1 = 0.0, c, x",
        "    elif hp < 4.0: r1, g1, bl1 = 0.0, x, c",
        "    elif hp < 5.0: r1, g1, bl1 = x, 0.0, c",
        "    else: r1, g1, bl1 = c, 0.0, x",
        "    m = b - c",
        "    return (int((r1 + m) * 255), int((g1 + m) * 255), int((bl1 + m) * 255), int((1.0 - t) * 255))",
        "",
        "def _hsbt_apply(sp):",
        "    p = sp.pen_params",
        "    sp.pen_color = _hsbt_to_rgba(p['color'], p['saturation'], p['brightness'], p['transparency'])",
        "",
        "def _rgba_to_hsbt(r, g, b, a=255):",
        "    r, g, b = r / 255.0, g / 255.0, b / 255.0",
        "    cmax = max(r, g, b)",
        "    cmin = min(r, g, b)",
        "    delta = cmax - cmin",
        "    brightness = cmax",
        "    saturation = 0.0 if cmax == 0 else delta / cmax",
        "    if delta == 0:",
        "        hue = 0.0",
        "    elif cmax == r:",
        "        hue = 60.0 * (((g - b) / delta) % 6)",
        "    elif cmax == g:",
        "        hue = 60.0 * (((b - r) / delta) + 2)",
        "    else:",
        "        hue = 60.0 * (((r - g) / delta) + 4)",
        "    hue = (hue / 360.0) * 100.0",
        "    return (hue % 100.0, saturation * 100.0, brightness * 100.0, (1.0 - a / 255.0) * 100.0)",
        "",
        "def _display_setcolor(sp, cv: Any):",
        "    if not sp: return",
        "    val = _str(cv).strip()",
        "    if val.startswith(\"#\"):",
        "        try:",
        "            h = val.lstrip(\"#\")",
        "            if len(h) == 6:",
        "                r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)",
        "                sp.pen_color = (r, g, b, 255)",
        "                h_, s, b_, t = _rgba_to_hsbt(r, g, b, 255)",
        "                sp.pen_params['color'] = h_",
        "                sp.pen_params['saturation'] = s",
        "                sp.pen_params['brightness'] = b_",
        "                sp.pen_params['transparency'] = t",
        "            elif len(h) == 8:",
        "                r, g, b, a = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)",
        "                sp.pen_color = (r, g, b, a)",
        "                h_, s, b_, t = _rgba_to_hsbt(r, g, b, a)",
        "                sp.pen_params['color'] = h_",
        "                sp.pen_params['saturation'] = s",
        "                sp.pen_params['brightness'] = b_",
        "                sp.pen_params['transparency'] = t",
        "        except Exception:",
        "            pass",
        "    else:",
        "        try:",
        "            _cv = int(float(_num(cv))) & 0xFFFFFFFF",
        "            _a = (_cv >> 24) & 0xFF",
        "            _r = (_cv >> 16) & 0xFF",
        "            _g = (_cv >> 8) & 0xFF",
        "            _b = _cv & 0xFF",
        "            sp.pen_color = (_r, _g, _b, _a if _a else 255)",
        "            h_, s, b_, t = _rgba_to_hsbt(_r, _g, _b, _a if _a else 255)",
        "            sp.pen_params['color'] = h_",
        "            sp.pen_params['saturation'] = s",
        "            sp.pen_params['brightness'] = b_",
        "            sp.pen_params['transparency'] = t",
        "        except (ValueError, TypeError):",
        "            pass",
        "",
        "def _display_setcolor_num(sp, cv: Any):",
        "    if sp:",
        "        # Legacy Scratch numeric pen color: integer 0-199 maps onto a",
        "        # 200-step color wheel (0 = red), full saturation/brightness.",
        "        _n = int(_num(cv)) % 200",
        "        _h = (_n / 200.0) * 360.0",
        "        sp.pen_color = _legacy_hsv_to_rgba(_h, 100.0, 100.0, 0.0)",
        "",
        "def _legacy_hsv_to_rgba(h, s, v, t):",
        "    # h in [0,360), s/v/t in [0,100]; standard HSV->RGB (Scratch legacy wheel)",
        "    _h = (h % 360.0) / 60.0",
        "    _s = max(0.0, min(100.0, s)) / 100.0",
        "    _v = max(0.0, min(100.0, v)) / 100.0",
        "    _c = _v * _s",
        "    _x = _c * (1.0 - abs((_h % 2.0) - 1.0))",
        "    _i = int(_h)",
        "    if _i == 0: r1, g1, b1 = _c, _x, 0.0",
        "    elif _i == 1: r1, g1, b1 = _x, _c, 0.0",
        "    elif _i == 2: r1, g1, b1 = 0.0, _c, _x",
        "    elif _i == 3: r1, g1, b1 = 0.0, _x, _c",
        "    elif _i == 4: r1, g1, b1 = _x, 0.0, _c",
        "    else: r1, g1, b1 = _c, 0.0, _x",
        "    _m = _v - _c",
        "    return (int((r1 + _m) * 255), int((g1 + _m) * 255), int((b1 + _m) * 255), 255)",
        "",
        "def _display_setsize(sp, sz: Any):",
        "    if sp: sp.pen_size = max(1, int(_num(sz)))",
        "",
        "def _display_setpencolorparam(sp, param: Any, val: Any):",
        "    if sp:",
        "        key = _str(param).lower()",
        "        if key in sp.pen_params:",
        "            if key == 'color':",
        "                sp.pen_params[key] = _num(val) % 100.0",
        "            else:",
        "                sp.pen_params[key] = max(0.0, min(100.0, _num(val)))",
        "            _hsbt_apply(sp)",
        "",
        "def _display_changepencolorparam(sp, param: Any, val: Any):",
        "    if sp:",
        "        key = _str(param).lower()",
        "        if key in sp.pen_params:",
        "            if key == 'color':",
        "                sp.pen_params[key] = (sp.pen_params[key] + _num(val)) % 100.0",
        "            else:",
        "                sp.pen_params[key] = max(0.0, min(100.0, sp.pen_params[key] + _num(val)))",
        "            _hsbt_apply(sp)",
        "",
        "",
        "def _load_list_asset(rel_path: str) -> list:",
        '    """Load a list previously externalised to data/<target>/<list>.json.',
        "    Falls back to an empty list if the asset is missing. Paths are",
        '    resolved relative to the module that imports this helper."""',
        "    import json as _j",
        "    try:",
        "        _here = Path(__file__).resolve().parent",
        "        _p = _here / rel_path",
        "        if not _p.exists():",
        "            _p = Path(rel_path)",
        "        if _p.exists():",
        "            with open(_p, encoding='utf-8') as _f:",
        "                return _j.load(_f)",
        "    except Exception:",
        "        pass",
        "    return []",
        "",
    ]
    out_path = output_dir / "_engine.py"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("emitted _engine.py")
    return out_path


def _generate_display(output_dir, opts=None):
    """Generate _display.py with the Tkinter/PIL Display class."""
    if opts is None:
        opts = {}
    scale = opts.get("scale", 2)
    stage_w = opts.get("stage_w", 480)
    stage_h = opts.get("stage_h", 360)
    output_dir = Path(output_dir)
    lines = [
        "# Auto-generated display module",
        "# Generator: decompile.py",
        '"""Tkinter-based Scratch stage renderer with pen support and stats overlay."""',
        "import math, re, time, xml.etree.ElementTree as ET",
        "from pathlib import Path",
        "import tkinter as tk",
        "from PIL import Image, ImageDraw, ImageTk, ImageFont",
        "try:",
        "    import numpy as _np",
        "except Exception:",
        "    _np = None",
        "_PIL_LANCZOS = Image.Resampling.LANCZOS",
        "_PIL_ROTRES = getattr(Image.Resampling, 'BICUBIC', _PIL_LANCZOS)",
        "",
        "",
        "# -------------------------------------------------------------------",
        "# Minimal SVG rasterizer (no cairo needed)",
        "# -------------------------------------------------------------------",
        "",
        "def _parse_color(s):",
        '    """Parse CSS color string to RGBA tuple."""',
        "    if not s or s == 'none':",
        "        return None",
        "    s = s.strip()",
        "    if s.startswith('#'):",
        "        h = s[1:]",
        "        if len(h) == 3:",
        "            h = h[0]*2+h[1]*2+h[2]*2",
        "        if len(h) >= 6:",
        "            return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16), 255)",
        "        if len(h) >= 8:",
        "            return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16), int(h[6:8],16))",
        "    m = re.match(r'rgba?\\s*\\(\\s*(\\d+\\.?\\d*)\\s*,\\s*(\\d+\\.?\\d*)\\s*,\\s*(\\d+\\.?\\d*)(?:\\s*,\\s*(\\d+\\.?\\d*))?\\s*\\)', s)",
        "    if m:",
        "        r,g,b = int(float(m.group(1))),int(float(m.group(2))),int(float(m.group(3)))",
        "        a = int(float(m.group(4))*255) if m.group(4) else 255",
        "        return (r,g,b,a)",
        "    named = {'white':(255,255,255,255),'black':(0,0,0,255),'red':(255,0,0,255),",
        "             'green':(0,128,0,255),'blue':(0,0,255,255),'yellow':(255,255,0,255),",
        "             'orange':(255,165,0,255),'transparent':(0,0,0,0)}",
        "    return named.get(s.lower(), (0,0,0,255))",
        "",
        "def _parse_svg_path(d):",
        '    """Parse SVG path d attribute into list of (cmd, args) tuples."""',
        "    tokens = re.findall(r'[MmZzLlHhVvCcSsQqTtAa]|[-+]?\\d*\\.?\\d+(?:[eE][-+]?\\d+)?', d)",
        "    i = 0",
        "    cmds = []",
        "    while i < len(tokens):",
        "        if tokens[i].isalpha():",
        "            cmd = tokens[i]; i += 1",
        "        else:",
        "            cmd = cmds[-1][0] if cmds else 'M'",
        "        nums = []",
        "        while i < len(tokens) and not tokens[i].isalpha():",
        "            try:",
        "                nums.append(float(tokens[i])); i += 1",
        "            except ValueError:",
        "                break",
        "        cmds.append((cmd, nums))",
        "    return cmds",
        "",
        "def _svg_arc_to(pts, x0, y0, rx, ry, phi_deg, large, sweep, x1, y1):",
        '    """Append sampled points for an SVG elliptical-arc endpoint pair."""',
        "    if rx == 0 or ry == 0:",
        "        pts.append((x1, y1)); return",
        "    rx = abs(rx); ry = abs(ry)",
        "    phi = math.radians(phi_deg)",
        "    cos_phi, sin_phi = math.cos(phi), math.sin(phi)",
        "    dx = (x0 - x1) / 2.0; dy = (y0 - y1) / 2.0",
        "    x1p = cos_phi * dx + sin_phi * dy",
        "    y1p = -sin_phi * dx + cos_phi * dy",
        "    # Correct out-of-range radii",
        "    _l = (x1p*x1p)/(rx*rx) + (y1p*y1p)/(ry*ry)",
        "    if _l > 1.0:",
        "        _sq = math.sqrt(_l); rx *= _sq; ry *= _sq",
        "    _sign = 1.0 if large != sweep else -1.0",
        "    _num = rx*rx*ry*ry - rx*rx*y1p*y1p - ry*ry*x1p*x1p",
        "    _den = rx*rx*y1p*y1p + ry*ry*x1p*x1p",
        "    _co = 0.0 if _den == 0 else _sign * math.sqrt(max(0.0, _num / _den))",
        "    cxp = _co * (rx*y1p/ry); cyp = _co * (-ry*x1p/rx)",
        "    cx_c = cos_phi * cxp - sin_phi * cyp + (x0 + x1) / 2.0",
        "    cy_c = sin_phi * cxp + cos_phi * cyp + (y0 + y1) / 2.0",
        "    def _ang(ux, uy):",
        "        _a = math.atan2(uy, ux)",
        "        return _a",
        "    _th1 = _ang((x1p - cxp) / rx, (y1p - cyp) / ry)",
        "    _v = ((-x1p - cxp) / rx, (-y1p - cyp) / ry)",
        "    _th2 = _ang(_v[0], _v[1])",
        "    _dth = _th2 - _th1",
        "    if sweep == 1 and _dth < 0: _dth += 2*math.pi",
        "    if sweep == 0 and _dth > 0: _dth -= 2*math.pi",
        "    if large:",
        "        if sweep == 1 and _dth < math.pi: _dth += 2*math.pi",
        "        if sweep == 0 and _dth > -math.pi: _dth -= 2*math.pi",
        "    _steps = max(8, int(abs(_dth) / 0.2) + 1)",
        "    for i in range(1, _steps + 1):",
        "        _t = _th1 + _dth * (i / _steps)",
        "        _x = cx_c + rx*math.cos(_t)*cos_phi - ry*math.sin(_t)*sin_phi",
        "        _y = cy_c + rx*math.cos(_t)*sin_phi + ry*math.sin(_t)*cos_phi",
        "        pts.append((_x, _y))",
        "",
        "def _eval_svg_path(d, scale=1.0, ox=0.0, oy=0.0):",
        '    """Convert SVG path d-string to list of (x,y) polygon points."""',
        "    cmds = _parse_svg_path(d)",
        "    pts = []",
        "    cx, cy = 0.0, 0.0",
        "    sx, sy = 0.0, 0.0",
        "    px, py = 0.0, 0.0",
        "    for cmd, args in cmds:",
        "        if cmd == 'M':",
        "            cx, cy = args[0]*scale+ox, args[1]*scale+oy; sx,sy=cx,cy; px,py=cx,cy",
        "            pts.append((cx, cy))",
        "        elif cmd == 'm':",
        "            cx += args[0]*scale; cy += args[1]*scale; sx,sy=cx,cy; px,py=cx,cy",
        "            pts.append((cx, cy))",
        "        elif cmd == 'L':",
        "            for j in range(0, len(args), 2):",
        "                cx, cy = args[j]*scale+ox, args[j+1]*scale+oy",
        "                pts.append((cx, cy))",
        "        elif cmd == 'l':",
        "            for j in range(0, len(args), 2):",
        "                cx += args[j]*scale; cy += args[j+1]*scale",
        "                pts.append((cx, cy))",
        "        elif cmd == 'H':",
        "            cx = args[0]*scale+ox; pts.append((cx, cy))",
        "        elif cmd == 'h':",
        "            cx += args[0]*scale; pts.append((cx, cy))",
        "        elif cmd == 'V':",
        "            cy = args[0]*scale+oy; pts.append((cx, cy))",
        "        elif cmd == 'v':",
        "            cy += args[0]*scale; pts.append((cx, cy))",
        "        elif cmd == 'C':",
        "            for j in range(0, len(args), 6):",
        "                x1,y1 = args[j]*scale+ox, args[j+1]*scale+oy",
        "                x2,y2 = args[j+2]*scale+ox, args[j+3]*scale+oy",
        "                ex,ey = args[j+4]*scale+ox, args[j+5]*scale+oy",
        "                for t_i in range(1, 11):",
        "                    t = t_i / 10.0",
        "                    mt = 1-t",
        "                    nx = mt**3*cx + 3*mt**2*t*x1 + 3*mt*t**2*x2 + t**3*ex",
        "                    ny = mt**3*cy + 3*mt**2*t*y1 + 3*mt*t**2*y2 + t**3*ey",
        "                    pts.append((nx, ny))",
        "                cx,cy = ex,ey; px,py = x2,y2",
        "        elif cmd == 'c':",
        "            for j in range(0, len(args), 6):",
        "                x1,y1 = cx+args[j]*scale, cy+args[j+1]*scale",
        "                x2,y2 = cx+args[j+2]*scale, cy+args[j+3]*scale",
        "                ex,ey = cx+args[j+4]*scale, cy+args[j+5]*scale",
        "                for t_i in range(1, 11):",
        "                    t = t_i / 10.0",
        "                    mt = 1-t",
        "                    nx = mt**3*cx + 3*mt**2*t*x1 + 3*mt*t**2*x2 + t**3*ex",
        "                    ny = mt**3*cy + 3*mt**2*t*y1 + 3*mt*t**2*y2 + t**3*ey",
        "                    pts.append((nx, ny))",
        "                cx,cy = ex,ey; px,py = x2,y2",
        "        elif cmd == 'S':",
        "            for j in range(0, len(args), 4):",
        "                x2,y2 = args[j]*scale+ox, args[j+1]*scale+oy",
        "                ex,ey = args[j+2]*scale+ox, args[j+3]*scale+oy",
        "                x1,y1 = 2*cx-x2, 2*cy-y2",
        "                for t_i in range(1, 11):",
        "                    t = t_i / 10.0; mt = 1-t",
        "                    nx = mt**3*cx + 3*mt**2*t*x1 + 3*mt*t**2*x2 + t**3*ex",
        "                    ny = mt**3*cy + 3*mt**2*t*y1 + 3*mt*t**2*y2 + t**3*ey",
        "                    pts.append((nx, ny))",
        "                cx,cy = ex,ey; px,py = x2,y2",
        "        elif cmd == 's':",
        "            for j in range(0, len(args), 4):",
        "                x2,y2 = cx+args[j]*scale, cy+args[j+1]*scale",
        "                ex,ey = cx+args[j+2]*scale, cy+args[j+3]*scale",
        "                x1,y1 = 2*cx-x2, 2*cy-y2",
        "                for t_i in range(1, 11):",
        "                    t = t_i / 10.0; mt = 1-t",
        "                    nx = mt**3*cx + 3*mt**2*t*x1 + 3*mt*t**2*x2 + t**3*ex",
        "                    ny = mt**3*cy + 3*mt**2*t*y1 + 3*mt*t**2*y2 + t**3*ey",
        "                    pts.append((nx, ny))",
        "                cx,cy = ex,ey; px,py = x2,y2",
        "        elif cmd in ('Q','T'):",
        "            for j in range(0, len(args), 4):",
        "                x1,y1 = args[j]*scale+ox, args[j+1]*scale+oy",
        "                ex,ey = args[j+2]*scale+ox, args[j+3]*scale+oy",
        "                for t_i in range(1, 11):",
        "                    t = t_i / 10.0; mt = 1-t",
        "                    nx = mt**2*cx + 2*mt*t*x1 + t**2*ex",
        "                    ny = mt**2*cy + 2*mt*t*y1 + t**2*ey",
        "                    pts.append((nx, ny))",
        "                cx,cy = ex,ey",
        "        elif cmd in ('q','t'):",
        "            for j in range(0, len(args), 4):",
        "                x1,y1 = cx+args[j]*scale, cy+args[j+1]*scale",
        "                ex,ey = cx+args[j+2]*scale, cy+args[j+3]*scale",
        "                for t_i in range(1, 11):",
        "                    t = t_i / 10.0; mt = 1-t",
        "                    nx = mt**2*cx + 2*mt*t*x1 + t**2*ex",
        "                    ny = mt**2*cy + 2*mt*t*y1 + t**2*ey",
        "                    pts.append((nx, ny))",
        "                cx,cy = ex,ey",
        "        elif cmd == 'A':",
        "            for j in range(0, len(args), 7):",
        "                ex,ey = args[j+5]*scale+ox, args[j+6]*scale+oy",
        "                _svg_arc_to(pts, cx, cy, args[j]*scale, args[j+1]*scale, args[j+2],",
        "                            1 if args[j+3] else 0, 1 if args[j+4] else 0, ex, ey)",
        "                cx,cy = ex,ey",
        "        elif cmd == 'a':",
        "            for j in range(0, len(args), 7):",
        "                ex,ey = cx+args[j+5]*scale, cy+args[j+6]*scale",
        "                _svg_arc_to(pts, cx, cy, args[j]*scale, args[j+1]*scale, args[j+2],",
        "                            1 if args[j+3] else 0, 1 if args[j+4] else 0, ex, ey)",
        "                cx,cy = ex,ey",
        "        elif cmd == 'Z' or cmd == 'z':",
        "            pts.append((sx, sy)); cx,cy = sx,sy",
        "    return pts",
        "",
        "def _render_svg_to_image(svg_path, scale=1.0):",
        '    """Render an SVG file to a PIL RGBA Image using resvg-py (Rust).',
        '    Raises ImportError with an install hint when resvg-py is missing."""',
        "    try:",
        "        from resvg_py import svg_to_bytes",
        "    except ImportError:",
        "        raise ImportError('resvg-py is required to render SVG costumes. Install with: pip install resvg-py. On Windows also need VC++ Redistributable from https://aka.ms/vs/17/release/vc_redist.x64.exe')",
        "    from PIL import Image as _PIL_Image",
        "    import io as _io",
        "    with open(svg_path, 'rb') as _f:",
        "        svg_str = _f.read().decode('utf-8')",
        "    return _PIL_Image.open(_io.BytesIO(svg_to_bytes(svg_string=svg_str, zoom=scale))).convert('RGBA')",
        "    _OLD_BODY_FOLLOWS = None  # placeholder; old stdlib body deleted below",
        "    ns = {'svg': 'http://www.w3.org/2000/svg'}",
        "    # get dimensions from root",
        "    vb = root.get('viewBox', '').split()",
        "    if len(vb) == 4:",
        "        vx, vy, vw, vh = [float(v) for v in vb]",
        "    else:",
        "        vw = float(root.get('width', '480').replace('px',''))",
        "        vh = float(root.get('height', '360').replace('px',''))",
        "        vx, vy = 0, 0",
        "    out_w = max(1, int(vw * scale))",
        "    out_h = max(1, int(vh * scale))",
        "    img = Image.new('RGBA', (out_w, out_h), (0,0,0,0))",
        "    draw = ImageDraw.Draw(img)",
        "    sx = out_w / vw",
        "    sy = out_h / vh",
        "",
        "    def _attr(el, name, default=None):",
        "        v = el.get(name)",
        "        if v is not None:",
        "            return v",
        "        style = el.get('style', '')",
        "        for part in style.split(';'):",
        "            if ':' in part:",
        "                k, val = part.split(':', 1)",
        "                if k.strip() == name:",
        "                    return val.strip()",
        "        return default",
        "",
        "    def _draw_el(el, inherited_fill=None, inherited_stroke=None, inherited_sw=None):",
        "        tag = el.tag.split('}')[-1] if '}' in el.tag else el.tag",
        "        fill = _attr(el, 'fill', inherited_fill)",
        "        stroke = _attr(el, 'stroke', inherited_stroke)",
        "        sw = _attr(el, 'stroke-width', inherited_sw)",
        "        sw_val = float(sw) * sx if (sw and sw.strip().lower() != 'none') else None",
        "        fill_c = _parse_color(fill)",
        "        stroke_c = _parse_color(stroke)",
        "        if fill and fill.strip() == 'none':",
        "            fill_c = None",
        "        if stroke and stroke.strip() == 'none':",
        "            stroke_c = None",
        "        opacity = _attr(el, 'opacity')",
        "",
        "        if tag == 'g':",
        "            nf = fill if fill else inherited_fill",
        "            ns2 = stroke if stroke else inherited_stroke",
        "            nsw = sw if sw else inherited_sw",
        "            for child in el:",
        "                _draw_el(child, nf, ns2, nsw)",
        "            return",
        "",
        "        if tag == 'path':",
        "            d = el.get('d', '')",
        "            if d:",
        "                pts = _eval_svg_path(d, sx, -vx*sx, -vy*sy)",
        "                int_pts = [(int(round(x)), int(round(y))) for x, y in pts]",
        "                if len(int_pts) >= 3:",
        "                    if fill_c:",
        "                        try:",
        "                            draw.polygon(int_pts, fill=fill_c)",
        "                        except Exception:",
        "                            pass",
        "                    if stroke_c and sw_val:",
        "                        try:",
        "                            draw.polygon(int_pts, outline=stroke_c)",
        "                        except Exception:",
        "                            pass",
        "                elif len(int_pts) == 2 and stroke_c:",
        "                    w = max(1, int(round(sw_val))) if sw_val else 1",
        "                    draw.line(int_pts, fill=stroke_c, width=w)",
        "",
        "        elif tag == 'rect':",
        "            x = float(_attr(el, 'x', '0')) * sx - vx*sx",
        "            y = float(_attr(el, 'y', '0')) * sy - vy*sy",
        "            w = float(_attr(el, 'width', '0')) * sx",
        "            h = float(_attr(el, 'height', '0')) * sy",
        "            box = [int(round(x)), int(round(y)), int(round(x+w)), int(round(y+h))]",
        "            if fill_c:",
        "                draw.rectangle(box, fill=fill_c)",
        "            if stroke_c and sw_val:",
        "                draw.rectangle(box, outline=stroke_c, width=max(1,int(round(sw_val))))",
        "",
        "        elif tag in ('circle', 'ellipse'):",
        "            cx = float(_attr(el, 'cx', '0')) * sx - vx*sx",
        "            cy = float(_attr(el, 'cy', '0')) * sy - vy*sy",
        "            if tag == 'circle':",
        "                r = float(_attr(el, 'r', '0')) * sx",
        "                rx, ry = r, r",
        "            else:",
        "                rx = float(_attr(el, 'rx', '0')) * sx",
        "                ry = float(_attr(el, 'ry', '0')) * sy",
        "            box = [int(round(cx-rx)), int(round(cy-ry)), int(round(cx+rx)), int(round(cy+ry))]",
        "            if fill_c:",
        "                draw.ellipse(box, fill=fill_c)",
        "            if stroke_c and sw_val:",
        "                draw.ellipse(box, outline=stroke_c, width=max(1,int(round(sw_val))))",
        "",
        "        elif tag == 'line':",
        "            x1 = float(_attr(el, 'x1', '0')) * sx - vx*sx",
        "            y1 = float(_attr(el, 'y1', '0')) * sy - vy*sy",
        "            x2 = float(_attr(el, 'x2', '0')) * sx - vx*sx",
        "            y2 = float(_attr(el, 'y2', '0')) * sy - vy*sy",
        "            w = max(1, int(round(sw_val))) if sw_val else 1",
        "            if stroke_c:",
        "                draw.line([(int(x1),int(y1)),(int(x2),int(y2))], fill=stroke_c, width=w)",
        "",
        "        elif tag == 'polygon':",
        "            pts_str = el.get('points', '')",
        "            nums = [float(x) for x in re.findall(r'[-+]?\\d*\\.?\\d+', pts_str)]",
        "            int_pts = [(int(round(nums[i]*sx-vx*sx)), int(round(nums[i+1]*sy-vy*sy))) for i in range(0,len(nums)-1,2)]",
        "            if len(int_pts) >= 3:",
        "                if fill_c:",
        "                    draw.polygon(int_pts, fill=fill_c)",
        "                if stroke_c and sw_val:",
        "                    draw.polygon(int_pts, outline=stroke_c)",
        "",
        "    for child in root:",
        "        _draw_el(child)",
        "    return img",
        "",
        "",
        "class Display:",
        '    """Manages a window that renders the Scratch stage and draws pen output."""',
        "",
        f"    def __init__(self, title=\"Scratch\", stage_size=({stage_w}, {stage_h}), scale={scale},",
        "                 assets_dir=None):",
        "        self.stage_w, self.stage_h = stage_size",
        "        self.scale = scale",
        "        self.win_w = stage_size[0] * scale",
        "        self.win_h = stage_size[1] * scale + 30",
        "",
        "        self.root = tk.Tk()",
        "        self.root.title(title)",
        '        self.root.protocol("WM_DELETE_WINDOW", self._on_close)',
        "        self.running = True",
        "        self.eng = None  # set by main.py to the owning Engine (click dispatch)",
        "        self.root.geometry(f\"{self.win_w}x{self.win_h}\")",
        "        self.root.resizable(False, False)",
        "",
        "        self.canvas = tk.Canvas(self.root, width=self.win_w, height=self.win_h,",
        '                                bg="#222", highlightthickness=0)',
        "        self.canvas.pack()",
        "",
        '        self.stage = Image.new("RGBA", (stage_size[0] * scale, stage_size[1] * scale), (220, 220, 220, 255))',
        "        self.pen_layer = Image.new('RGBA', (stage_size[0] * scale, stage_size[1] * scale), (0, 0, 0, 0))",
        "        self._photo = None",
        "        self._image_item = None",
        "        self._stats_item = None",
        "",

        "        # backdrop cache: avoid recompositing when stage+pen unchanged",
        "        self._backdrop_cache_key = None",
        "        self._backdrop_cache_img = None",
        "        self._pen_layer_version = 0",
        "",
        "        # pen state",
        "        self.pen_down = False",
        "        self.pen_color = (0, 0, 0, 255)",
        "        self.pen_size = 1",
        "        self.last_pen_pos = None",
        "        self.pen_params = {'color': 0.0, 'saturation': 100.0, 'brightness': 50.0, 'transparency': 0.0}",
        "",
        "        # costume cache",
        "        self._assets_dir = Path(assets_dir) if assets_dir else Path.cwd()",
        "        self._costume_cache = {}",
        "        # bounded scaled-cache: key (md5, w, h) -> image; evict LRU when large",
        "        self._costume_scaled_cache = {}",
        "        self._costume_scaled_cache_max = 256",
        "",
        "        # input state",
        "        self.key_state = {}",
        '        self.mouse_state = {"x": 0.0, "y": 0.0, "down": False}',
        "        self._ask_var = None",
        "        self._ask_answer = None",
        "        # FIFO of pending ask tokens so concurrent 'ask and wait' blocks",
        "        # from multiple sprites are serialized instead of clobbering the",
        "        # single shared input widget.",
        "        self._ask_waiters = []",
        "        # composite frame buffer of the previous rendered frame (used by",
        "        # color-sensing so it can detect sprites and pen trails, not just",
        "        # the static backdrop)",
        "        self._last_frame = None",
        "",
        "        self._bind_events()",
        "        self.root.update()",
        "",
        "    def _bind_events(self):",
        '        self.canvas.bind("<Motion>", self._on_mouse_move)',
        '        self.canvas.bind("<Button-1>", self._on_mouse_down)',
        '        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)',
        '        self.root.bind("<KeyPress>", self._on_key_down)',
        '        self.root.bind("<KeyRelease>", self._on_key_up)',
        "",
        "    def _scratch_xy(self, canvas_x, canvas_y):",
        "        # The stage image is drawn from the top-left (0,0); the stats bar sits",
        "        # below the stage, so no vertical offset is needed here.",
        "        sy = canvas_y / self.scale",
        "        sx = canvas_x / self.scale",
        "        return (sx - self.stage_w / 2, self.stage_h / 2 - sy)",
        "",
        "    def _on_mouse_move(self, e):",
        '        self.mouse_state["x"], self.mouse_state["y"] = self._scratch_xy(e.x, e.y)',
"        if getattr(self, \"_drag_target\", None) is not None:",
"            # Update target coordinates directly to match mouse pointer",
"            self._drag_target.x = self.mouse_state[\"x\"]",
"            self._drag_target.y = self.mouse_state[\"y\"]",
"        ",
"    def _on_mouse_down(self, e):",
        '        self.mouse_state["down"] = True',
        "        if self.eng is not None:",
        "            _hit = self._hit_test_sprite(e.x, e.y)",
        "            if _hit is not None:",
        "                if getattr(_hit, \"is_draggable\", False) or getattr(_hit, \"_dragging\", False):",
        "                    _hit._dragging = True",
        "                    self._drag_target = _hit",
        "                self.eng._fire_event(\"event_whenthisspriteclicked\", _hit)",
        "            else:",
        "                self.eng._fire_event(\"event_whenstageclicked\")",
        "",
        "    def _on_mouse_up(self, e):",
        '        self.mouse_state["down"] = False',
        "        _dt = getattr(self, \"_drag_target\", None)",
        "        if _dt is not None:",
        "            _dt._dragging = False",
        "            self._drag_target = None",
        "",
        "    def _hit_test_sprite(self, ex, ey):",
        '        """Return the topmost visible sprite whose (drawn) bbox contains the click, else None."""',
        "        try:",
        "            _sx = self.scale",
        "            _gx = (ex / _sx) - self.stage_w / 2.0",
        "            _gy = self.stage_h / 2.0 - (ey / _sx)",
        "            _eng = self.eng",
        "            if _eng is None:",
        "                return None",
        "            _cands = [s for s in _eng.sprites.values() if not getattr(s, 'is_stage', False) and getattr(s, 'visible', True)]",
        "            _cands.sort(key=lambda s: getattr(s, 'z_index', 0), reverse=True)",
        "            for _sp in _cands:",
        "                _md5 = getattr(_sp, '_costume_md5', None)",
        "                if not _md5:",
        "                    continue",
         "                _img = self.load_costume(_md5, getattr(_sp, 'name', None))",
         "                if _img is None:",
        "                    continue",
        "                _s = max(0.0, getattr(_sp, 'size', 100.0)) / 100.0",
        "                _nw = max(1, int(_img.size[0] * _s))",
        "                _nh = max(1, int(_img.size[1] * _s))",
        "                _hw = _nw / (2.0 * _sx)",
        "                _hh = _nh / (2.0 * _sx)",
        "                if _hw <= 0 or _hh <= 0:",
        "                    continue",
        "                if abs(_gx - _sp.x) <= _hw and abs(_gy - _sp.y) <= _hh:",
        "                    return _sp",
        "        except Exception:",
        "            pass",
        "        return None",
        "",
        "    _SCRATCH_KEYS = {",
        "        'Up': 'up arrow', 'Down': 'down arrow',",
        "        'Left': 'left arrow', 'Right': 'right arrow',",
        "        'space': 'space', 'Return': 'enter',",
        "        'Escape': 'esc', 'Tab': 'tab',",
        "        'BackSpace': 'backspace', 'Delete': 'delete',",
        "        'Shift_L': 'shift', 'Shift_R': 'shift',",
        "        'Control_L': 'ctrl', 'Control_R': 'ctrl',",
        "        'Alt_L': 'alt', 'Alt_R': 'alt',",
        "    }",
        "",
        "    def _key_id(self, e):",
        '        """Stable, modifier-invariant key identity from the virtual keycode."""',
        "        _kc = getattr(e, 'keycode', None)",
        "        # Map common keycodes to canonical Scratch key names",
        "        _by_code = {",
        "            32: 'space', 13: 'enter', 8: 'backspace', 9: 'tab', 27: 'esc',",
        "            127: 'delete', 46: 'delete', 277: 'delete',",
        "            37: 'left arrow', 38: 'up arrow', 39: 'right arrow', 40: 'down arrow',",
        "            65: 'a', 66: 'b', 67: 'c', 68: 'd', 69: 'e', 70: 'f', 71: 'g',",
        "            72: 'h', 73: 'i', 74: 'j', 75: 'k', 76: 'l', 77: 'm', 78: 'n',",
        "            79: 'o', 80: 'p', 81: 'q', 82: 'r', 83: 's', 84: 't', 85: 'u',",
        "            86: 'v', 87: 'w', 88: 'x', 89: 'y', 90: 'z',",
        "            48: '0', 49: '1', 50: '2', 51: '3', 52: '4', 53: '5', 54: '6',",
        "            55: '7', 56: '8', 57: '9',",
        "        }",
        "        if _kc in _by_code:",
        "            return _by_code[_kc]",
        "        # NumPad / other: fall back to a lowercased symbol (modifier-invariant)",
        "        _name = self._SCRATCH_KEYS.get(getattr(e, 'keysym', ''), getattr(e, 'keysym', '').lower())",
        "        return _name or ('key%d' % _kc if _kc else 'unknown')",
        "",
        "    def _on_key_down(self, e):",
        "        k = self._key_id(e)",
        "        self.key_state[k] = True",
        "",
        "    def _on_key_up(self, e):",
        "        k = self._key_id(e)",
        "        self.key_state[k] = False",
        "",
        "    def ask_async(self, question: str):",
        '        """Non-blocking ask: show an input box at the bottom of the stage.',
        '        Returns a generator that yields until Enter is pressed, then',
        '        yields the final answer string. The mainloop keeps running.',
        '        Concurrent asks are serialized via a FIFO queue so they never',
        '        overwrite the shared input widget used by another sprite."""',
        "        _tok = object()",
        "        self._ask_waiters.append(_tok)",
        "        # Wait until it is our turn (front of the queue).",
        "        while self._ask_waiters and self._ask_waiters[0] is not _tok:",
        "            yield",
        "        if self._ask_var is None:",
        "            self._ask_var = tk.StringVar()",
        "            _x = 8",
        "            _y = self.win_h - 28",
        "            self._ask_label = tk.Label(self.root, text='', anchor='w',",
        "                                     bg='#222', fg='#fff',",
        "                                     font=('Consolas', 11))",
        "            self._ask_label.place(x=_x, y=_y)",
        "            self._ask_entry = tk.Entry(self.root, textvariable=self._ask_var,",
        "                                      font=('Consolas', 11), bg='#fff', fg='#000')",
        "            self._ask_entry.place(x=_x + 4, y=_y + 22, width=self.win_w - 200)",
        "            self._ask_entry.bind('<Return>', self._on_ask_return)",
        "        self._ask_label.config(text=str(question))",
        "        self._ask_answer = None",
        "        self._ask_entry.delete(0, tk.END)",
        "        self._ask_entry.focus_set()",
        "        while self._ask_answer is None:",
        "            yield",
        "        self._ask_label.config(text='')",
        "        _ans = self._ask_answer",
        "        self._ask_answer = None",
        "        # Release our slot and hand off to the next queued ask.",
        "        if self._ask_waiters and self._ask_waiters[0] is _tok:",
        "            self._ask_waiters.pop(0)",
        "        return _ans",
        "",
        "    def _on_ask_return(self, e):",
        "        self._ask_answer = self._ask_var.get()",
        "",
        "    def hide_ask(self):",
        '        """Hide the ask input overlay if it exists."""',
        "        if getattr(self, '_ask_entry', None) is not None:",
        "            self._ask_entry.place_forget()",
        "            self._ask_label.place_forget()",
        "            self._ask_answer = ''",
        "        # Drop any queued asks (e.g. on green-flag restart).",
        "        self._ask_waiters = []",
        "",
        "    def _on_close(self):",
        "        self.running = False",
        "        self.root.destroy()",
        "",
        "    def load_costume(self, md5ext: str, sprite_name: str = None) -> Image.Image | None:",
        "        _key = (sprite_name or '', md5ext)",
        "        if _key in self._costume_cache:",
        "            return self._costume_cache[_key]",
        "        data_dir = self._assets_dir / 'data'",
        "        if not hasattr(self, '_file_index'):",
        "            self._file_index = {}",
        "            if data_dir.is_dir():",
        "                for sub in data_dir.iterdir():",
        "                    if sub.is_dir():",
        "                        for f in sub.iterdir():",
        "                            if f.is_file():",
        "                                self._file_index[(sub.name, f.name)] = f",
        "        path = None",
        "        if sprite_name:",
        "            path = self._file_index.get((sprite_name, md5ext))",
        "        if path is None:",
        "            for (_sn, _fn), _p in self._file_index.items():",
"                if _fn == md5ext and _sn != sprite_name:",
"                    path = _p",
"                    break",
        "        if path is None:",
        "            _fb = self._assets_dir / md5ext",
        "            if _fb.exists():",
        "                path = _fb",
        "        if path is None:",
        "            return None",
        "        candidates = [path]",
        "        for path in candidates:",
        "            if path.exists():",
        "                try:",
        "                    if path.suffix.lower() == '.svg':",
        "                        img = _render_svg_to_image(str(path), scale=self.scale)",
        "                    else:",
        "                        raw = Image.open(path).convert('RGBA')",
        "                        w, h = raw.size",
        "                        ns = self.scale",
        "                        if ns > 1 and (w * ns != w or h * ns != h):",
        "                            img = raw.resize((w * ns, h * ns), _PIL_LANCZOS)",
        "                        else:",
        "                            img = raw",
        "                    if img is not None:",
        "                        self._costume_cache[_key] = img",
        "                        return img",
        "                except Exception:",
        "                    pass",
        "        return None",
        "",
        "    def _composite_array(self, dst, src, ox=0, oy=0):",
        '        """Vectorized alpha blend of src (PIL RGBA) onto dst',
        '        (HxWx4 uint8 numpy array) at offset (ox, oy). No Python loops."""',
        "        if _np is None:",
        "            try:",
        "                dst_img = Image.fromarray(dst.astype(_np.uint8) if hasattr(dst, 'astype') else dst)",
        "                dst_img.paste(src, (ox, oy), src)",
        "            except Exception:",
        "                pass",
        "            return",
        "        _s = _np.asarray(src.convert('RGBA'))",
        "        _sh, _sw = _s.shape[:2]",
        "        _dh, _dw = dst.shape[:2]",
        "        _x0 = max(0, ox); _y0 = max(0, oy)",
        "        _x1 = min(_dw, ox + _sw); _y1 = min(_dh, oy + _sh)",
        "        if _x1 <= _x0 or _y1 <= _y0:",
        "            return",
        "        _sx0 = _x0 - ox; _sy0 = _y0 - oy",
        "        _sx1 = _sx0 + (_x1 - _x0); _sy1 = _sy0 + (_y1 - _y0)",
        "        _src = _s[_sy0:_sy1, _sx0:_sx1]",
        "        _dst = dst[_y0:_y1, _x0:_x1]",
        "        _a = _src[:, :, 3]",
        "        # Fast path: fully transparent source -> nothing to draw.",
        "        if _a.max() == 0:",
        "            return",
        "        # Fast path: fully opaque source over the whole region -> just copy.",
        "        if _a.min() == 255:",
        "            _dst[...] = _src",
        "            return",
        "        # straight-over alpha compositing in integer 0-255 space (vectorized).",
        "        _ia = 255 - _a",
        "        _f = (_src.astype(_np.uint16) * _a[..., None] + _dst.astype(_np.uint16) * _ia[..., None] + 127) // 255",
        "        _dst[..., :3] = _f[..., :3].astype(_np.uint8)",
        "        _dst[..., 3] = _np.maximum(_dst[..., 3], _a).astype(_np.uint8)",
        "",
        "    def _apply_effects(self, img, effects):",
        '        """Apply Scratch graphic effects (ghost, color, brightness, fisheye, whirl, pixelate, mosaic)."""',
        "        if not effects:",
        "            return img",
        "        effects = {str(k).lower(): v for k, v in effects.items()}",
        "        _rimg = img",
        "        if _rimg.mode != 'RGBA':",
        "            _rimg = _rimg.convert('RGBA')",
        "        _ghost = effects.get('ghost', 0)",
        "        if _ghost:",
        "            _ghost = max(0.0, min(100.0, float(_ghost)))",
        "            _af = max(0.0, min(1.0, (100.0 - _ghost) / 100.0))",
        "            _r, _g, _b, _a = _rimg.split()",
        "            _a = _a.point(lambda p: int(p * _af))",
        "            _rimg = Image.merge('RGBA', (_r, _g, _b, _a))",
        "        _cs = effects.get('color', 0)",
        "        if _cs:",
        "            _cs = float(_cs)",
        "            _hsv = _rimg.convert('HSV')",
        "            _h, _s, _v = _hsv.split()",
        "            _h = _h.point(lambda p: int((p + int(_cs * 255.0 / 100.0)) % 256))",
        "            _rimg = Image.merge('HSV', (_h, _s, _v)).convert('RGBA')",
        "        _br = effects.get('brightness', 0)",
        "        if _br:",
        "            _br = max(-100.0, min(100.0, float(_br)))",
        "            _f = 1.0 + _br / 100.0",
        "            _r, _g, _b, _a = _rimg.split()",
        "            _r = _r.point(lambda p: max(0, min(255, int(p * _f))))",
        "            _g = _g.point(lambda p: max(0, min(255, int(p * _f))))",
        "            _b = _b.point(lambda p: max(0, min(255, int(p * _f))))",
        "            _rimg = Image.merge('RGBA', (_r, _g, _b, _a))",
        "        _fe = effects.get('fisheye', 0)",
        "        if _fe:",
        "            _fe = max(-100.0, min(100.0, float(_fe)))",
        "            _rimg = _rimg.convert('RGBA')",
        "            _w, _h = _rimg.size",
        "            _cx, _cy = _w // 2, _h // 2",
        "            _max_r = max(_cx, _cy)",
        "            _k = _fe / 100.0 * 0.5",
        "            if _np is not None:",
        "                try:",
        "                    _arr = _np.asarray(_rimg)",
        "                    _yy, _xx = _np.mgrid[0:_h, 0:_w]",
        "                    _dx = _xx.astype(_np.float64) - _cx",
        "                    _dy = _yy.astype(_np.float64) - _cy",
        "                    _d = _np.hypot(_dx, _dy)",
        "                    _d_safe = _np.where(_d == 0, 1.0, _d)",
        "                    _r = _d / _max_r",
        "                    _nr = _r * (1.0 + _k * _r)",
        "                    _sx = _np.clip((_cx + _dx * _nr / _d_safe).astype(_np.int32), 0, _w - 1)",
        "                    _sy = _np.clip((_cy + _dy * _nr / _d_safe).astype(_np.int32), 0, _h - 1)",
        "                    _out = _arr[_sy, _sx]",
        "                    _out[_nr > 1.0] = [0, 0, 0, 0]",
        "                    _rimg = Image.fromarray(_out, 'RGBA')",
        "                except Exception:",
        "                    pass",
        "        _wh = effects.get('whirl', 0)",
        "        if _wh:",
        "            _wh = max(-100.0, min(100.0, float(_wh)))",
        "            _rimg = _rimg.convert('RGBA')",
        "            _w, _h = _rimg.size",
        "            _cx, _cy = _w // 2, _h // 2",
        "            _angle = _wh / 100.0 * math.pi * 2.0",
        "            if _np is not None:",
        "                try:",
        "                    _arr = _np.asarray(_rimg)",
        "                    _yy, _xx = _np.mgrid[0:_h, 0:_w]",
        "                    _dx = _xx.astype(_np.float64) - _cx",
        "                    _dy = _yy.astype(_np.float64) - _cy",
        "                    _d = _np.hypot(_dx, _dy)",
        "                    _max_r = max(_cx, _cy)",
        "                    _r = _d / _max_r",
        "                    _theta = _np.arctan2(_dy, _dx) + _angle * _r",
        "                    _sx = _np.clip((_cx + _d * _np.cos(_theta)).astype(_np.int32), 0, _w - 1)",
        "                    _sy = _np.clip((_cy + _d * _np.sin(_theta)).astype(_np.int32), 0, _h - 1)",
        "                    _rimg = Image.fromarray(_arr[_sy, _sx], 'RGBA')",
        "                except Exception:",
        "                    pass",
        "        _px = effects.get('pixelate', 0)",
        "        if _px:",
        "            _px = max(1, int(_px))",
        "            _rimg = _rimg.resize((max(1, _rimg.width // _px), max(1, _rimg.height // _px)), Image.NEAREST)",
        "            _rimg = _rimg.resize((_rimg.width * _px, _rimg.height * _px), Image.NEAREST)",
        "        _mo = effects.get('mosaic', 0)",
        "        if _mo:",
        "            _mo = max(1, int(_mo))",
        "            _rimg = _rimg.resize((max(1, _rimg.width // _mo), max(1, _rimg.height // _mo)), Image.NEAREST)",
        "            _rimg = _rimg.resize((_rimg.width * _mo, _rimg.height * _mo), Image.NEAREST)",
        "        return _rimg",
        "",
        "    def _cache_scaled(self, key, img):",
        "        cache = self._costume_scaled_cache",
        "        if len(cache) >= self._costume_scaled_cache_max:",
        "            # evict the oldest inserted key (simple LRU by insertion order)",
        "            try:",
        "                cache.pop(next(iter(cache)))",
        "            except Exception:",
        "                pass",
        "        cache[key] = img",
        "        return img",
        "",
        "    def get_scaled(self, md5ext: str, nw: int, nh: int, img=None):",
        '        """Return a cached (or freshly resized) scaled costume image."""',
        "        _key = (md5ext, nw, nh)",
        "        _sc = self._costume_scaled_cache.get(_key)",
        "        if _sc is not None:",
        "            return _sc",
        "        if img is None:",
        "            img = self.load_costume(md5ext)",
        "        if img is None:",
        "            return None",
        "        if (nw, nh) != img.size:",
        "            try:",
        "                _sc = img.resize((nw, nh), _PIL_LANCZOS)",
        "            except Exception:",
        "                _sc = img",
        "            return self._cache_scaled(_key, _sc)",
        "        return img",
        "",
        "    def clear(self):",
        "        w = self.stage_w * self.scale",
        "        h = self.stage_h * self.scale",
        "        self.stage = Image.new('RGBA', (w, h), (220, 220, 220, 255))",
        "        self.pen_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0)); self._pen_layer_version += 1",
        "        self._costume_scaled_cache.clear()",
        "",
        "    def stamp(self, md5ext: str, x: float, y: float, size_pct: float, sprite_name=None):",
        "        img = self.load_costume(md5ext, sprite_name)",
        "        if img is None:",
        "            return",
        "        s = size_pct / 100.0",
        "        w, h = img.size",
        "        nw = max(1, int(w * s))",
        "        nh = max(1, int(h * s))",
        "        if (nw, nh) != (w, h):",
        "            img = img.resize((nw, nh), _PIL_LANCZOS)",
        "        sx, sy = self.scale, self.scale",
        "        px = int((x + self.stage_w / 2) * sx - nw / 2)",
        "        py = int((self.stage_h / 2 - y) * sy - nh / 2)",
        "        self.pen_layer.paste(img, (px, py), img); self._pen_layer_version += 1",
        "",
        "    def draw_line(self, sp, x1: float, y1: float, x2: float, y2: float):",
        "        draw = ImageDraw.Draw(self.pen_layer)",
        "        sx, sy = self.scale, self.scale",
        "        px1 = int((x1 + self.stage_w / 2) * sx)",
        "        py1 = int((self.stage_h / 2 - y1) * sy)",
        "        px2 = int((x2 + self.stage_w / 2) * sx)",
        "        py2 = int((self.stage_h / 2 - y2) * sy)",
        "        color = getattr(sp, \"pen_color\", (0, 0, 0, 255))",
        "        size = getattr(sp, \"pen_size\", 1)",
        "        color = self._norm_pen_color(color)",
        "        w = max(1, int(size * sx))",
        "        if len(color) == 4 and color[3] < 255:",
        "            # bbox-trimmed alpha composite: only allocate the bbox around the line",
        "            _pad = max(w, 2)",
        "            _lx0 = max(0, min(px1, px2) - _pad)",
        "            _ly0 = max(0, min(py1, py2) - _pad)",
        "            _lx1 = min(self.pen_layer.size[0], max(px1, px2) + _pad + 1)",
        "            _ly1 = min(self.pen_layer.size[1], max(py1, py2) + _pad + 1)",
        "            if _lx1 <= _lx0 or _ly1 <= _ly0:",
        "                return",
        "            _bw = _lx1 - _lx0; _bh = _ly1 - _ly0",
        "            layer = Image.new('RGBA', (_bw, _bh), (0, 0, 0, 0))",
        "            ImageDraw.Draw(layer).line([(px1 - _lx0, py1 - _ly0), (px2 - _lx0, py2 - _ly0)], fill=color, width=w)",
        "            self.pen_layer.alpha_composite(layer, dest=(_lx0, _ly0)); self._pen_layer_version += 1",
        "        else:",
        "            draw.line([(px1, py1), (px2, py2)], fill=color, width=w); self._pen_layer_version += 1",
        "",
        "    def draw_point(self, sp, x: float, y: float):",
        "        sx, sy = self.scale, self.scale",
        "        px = int((x + self.stage_w / 2) * sx)",
        "        py = int((self.stage_h / 2 - y) * sy)",
        "        # Honor the sprite's own pen color/size (fall back to Display defaults).",
        "        color = getattr(sp, 'pen_color', None) if sp is not None else None",
        "        if color is None:",
        "            color = self.pen_color",
        "        size = getattr(sp, 'pen_size', None) if sp is not None else None",
        "        if size is None:",
        "            size = self.pen_size",
        "        color = self._norm_pen_color(color)",
        "        r = max(1, int(size * sx) // 2)",
        "        box = (px - r, py - r, px + r, py + r)",
        "        if len(color) == 4 and color[3] < 255:",
        "            # bbox-trimmed alpha composite: only allocate the bbox around the point/ellipse",
        "            _bw = box[2] - box[0]; _bh = box[3] - box[1]",
        "            if _bw <= 0 or _bh <= 0:",
        "                return",
        "            _x0 = max(0, box[0]); _y0 = max(0, box[1])",
        "            _sx0 = box[0] - _x0; _sy0 = box[1] - _y0",
        "            _sx1 = min(_bw, box[2] - _x0); _sy1 = min(_bh, box[3] - _y0)",
        "            layer = Image.new('RGBA', (_bw, _bh), (0, 0, 0, 0))",
        "            ImageDraw.Draw(layer).ellipse((_sx0, _sy0, _sx1, _sy1), fill=color)",
        "            self.pen_layer.alpha_composite(layer, dest=(_x0, _y0)); self._pen_layer_version += 1",
        "        else:",
        "            ImageDraw.Draw(self.pen_layer).ellipse(box, fill=color); self._pen_layer_version += 1",
        "",
        "    @staticmethod",
        "    def _norm_pen_color(color):",
        "        \"\"\"Coerce a pen color to a clamped int RGBA tuple.\"\"\"",
        "        if color is None:",
        "            return (0, 0, 0, 255)",
        "        try:",
        "            c = list(color)",
        "        except TypeError:",
        "            return (0, 0, 0, 255)",
        "        if len(c) == 3:",
        "            c.append(255)",
        "        c = c[:4]",
        "        return tuple(max(0, min(255, int(round(v)))) for v in c)",
        "",
        "    def pen_move(self, sp, nx: float, ny: float):",
        "        \"\"\"Draw a line from the sprite's last pen position to (nx, ny)\"\"\"",
        "        \"\"\"when the pen is down (Scratch pen semantics).\"\"\"",
        "        if sp is not None and getattr(sp, 'pen_down', False):",
        "            try:",
        "                if sp._px == nx and sp._py == ny:",
        "                    # Zero-length move: PIL draws nothing for a degenerate",
        "                    # line, so emit a dot instead (Scratch draws a point).",
        "                    self.draw_point(sp, nx, ny)",
        "                else:",
        "                    self.draw_line(sp, sp._px, sp._py, nx, ny)",
        "            except Exception:",
        "                pass",
        "        if sp is not None:",
        "            sp._px, sp._py = nx, ny",
        "",
        "    def _draw_bubble(self, frame, draw, sp):",
        '        """Draw a speech/think bubble for sp above its costume."""',
        "        txt = getattr(sp, '_say_text', '') or ''",
        "        if not txt:",
        "            return",
        "        kind = getattr(sp, '_say_type', 'say') or 'say'",
        "        sx, sy = self.scale, self.scale",
        "        nw = int((sp.x + self.stage_w / 2) * sx)",
        "        nh_top = int((self.stage_h / 2 - sp.y) * sy)",
        "        try:",
        "            font = ImageFont.truetype('arial.ttf', max(11, int(13 * sx)))",
        "        except Exception:",
        "            font = ImageFont.load_default()",
        "        pad = int(8 * sx)",
        "        max_w = int(self.stage_w * sx * 0.6)",
        "        # wrap text to max_w",
        "        words = str(txt).split()",
        "        lines = []",
        "        cur = ''",
        "        for wd in words:",
        "            test = (cur + ' ' + wd).strip()",
        "            if draw.textlength(test, font=font) <= max_w or not cur:",
        "                cur = test",
        "            else:",
        "                lines.append(cur); cur = wd",
        "        if cur:",
        "            lines.append(cur)",
        "        if not lines:",
        "            lines = [str(txt)]",
        "        lh = int(getattr(font, 'size', font.getbbox('A')[3]) * 1.25)",
        "        tw = max(draw.textlength(ln, font=font) for ln in lines)",
        "        bw = int(tw + pad * 2)",
        "        bh = int(lh * len(lines) + pad * 2)",
        "        # bubble position: above sprite, clamped to stage",
        "        bx = max(2, min(int(self.stage_w * sx) - bw - 2, nw - bw // 2))",
        "        by = max(2, nh_top - bh - int(18 * sx))",
        "        if kind == 'think':",
        "            fill_c = (255, 255, 255, 235)",
        "            out_c = (40, 40, 40, 255)",
        "        else:",
        "            fill_c = (255, 255, 255, 240)",
        "            out_c = (40, 40, 40, 255)",
        "        draw.rounded_rectangle([bx, by, bx + bw, by + bh],",
        "                              radius=int(10 * sx), fill=fill_c, outline=out_c, width=max(1, int(sx)))",
        "        # little tail pointing down to sprite",
        "        tx = max(bx + 6, min(bx + bw - 6, nw))",
        "        draw.polygon([(tx - int(6*sx), by + bh), (tx + int(6*sx), by + bh),",
        "                     (tx, by + bh + int(12 * sx))], fill=fill_c)",
        "        ty = by + pad",
        "        for ln in lines:",
        "            draw.text((bx + pad, ty), ln, fill=(20, 20, 20, 255), font=font)",
        "            ty += lh",
        "",
        "    def render(self, sprites: dict, stats: dict):",
        '        """Composite the final frame: backdrop → pen → sprites (z-sorted)."""',
        "        # Accept either a dict (name->sprite) or a list of sprites.",
        "        if isinstance(sprites, dict):",
        "            _sprites = list(sprites.values())",
        "        else:",
        "            _sprites = list(sprites)",
        "        w = self.stage_w * self.scale",
        "        h = self.stage_h * self.scale",
        "        # Backdrop cache: check if stage backdrop + pen layer unchanged.",
        "        _stage_md5 = None",
        "        for sp in _sprites:",
        "            if sp.is_stage:",
        "                _stage_md5 = getattr(sp, '_costume_md5', None)",
        "                break",
        "        _cache_key = (_stage_md5, self._pen_layer_version)",
        "        if self._backdrop_cache_key == _cache_key and self._backdrop_cache_img is not None:",
        "            frame = self._backdrop_cache_img.copy()",
        "            use_numpy = False",
        "            draw = ImageDraw.Draw(frame)",
        "        else:",
        "            frame = self.stage.copy()",
        "            # GPU-style accelerated compositing: blend backdrop + pen layer",
        "            # into a single numpy array (C-level, no Python pixel loops).",
        "            if _np is not None:",
        "                _arr = _np.asarray(frame).astype(_np.uint8)",
        "                for sp in _sprites:",
        "                    if sp.is_stage:",
        '                        md5 = getattr(sp, "_costume_md5", None)',
        "                        if md5:",
        "                            _bimg = self.load_costume(md5)",
        "                            if _bimg is not None and _bimg.size != (w, h):",
        "                                _bimg = self.get_scaled(md5, int(w), int(h), _bimg)",
        "                            if _bimg is not None:",
        "                                self._composite_array(_arr, _bimg, 0, 0)",
        "                        break",
        "                    self._composite_array(_arr, self.pen_layer)",
"                    use_numpy = True",
"            else:",
"                for sp in _sprites:",
        "                    if sp.is_stage:",
        '                        md5 = getattr(sp, "_costume_md5", None)',
        "                        if md5:",
        "                            _bimg = self.load_costume(md5)",
        "                            if _bimg is not None:",
        "                                if _bimg.size != (w, h):",
        "                                    _bimg = _bimg.resize((w, h), _PIL_LANCZOS)",
        "                                frame.paste(_bimg, (0, 0), _bimg)",
        "                        break",
        "                    frame.paste(self.pen_layer, (0, 0), self.pen_layer)",
        "            draw = ImageDraw.Draw(frame)",
        "            use_numpy = False",
        "        if use_numpy:",
        "            draw = None",
        "",
        "        ordered = sorted(_sprites, key=lambda sp: getattr(sp, 'z_index', 0))",
        "        for sp in ordered:",
        "            if not sp.visible or sp.is_stage:",
        "                continue",
        '            md5 = getattr(sp, "_costume_md5", None)',
        "            if not md5:",
         "                continue",
         "            img = self.load_costume(md5, getattr(sp, 'name', None))",
         "            if img is None:",
        "                continue",
        "            s = max(0.0, sp.size) / 100.0",
        "            w2, h2 = img.size",
        "            nw = max(1, int(w2 * s))",
        "            nh = max(1, int(h2 * s))",
        "            img = self.get_scaled(md5, nw, nh, img)",
        "            sx, sy = self.scale, self.scale",
        "            # Honor the costume's rotation center (off-center origins).",
        "            # _rc coords are in native (unscaled) costume-pixel space.",
        "            _rc = sp._costume_rotation_center()",
        "            if _rc is not None:",
        "                _rcx = _rc[0] * self.scale * s",
        "                _rcy = _rc[1] * self.scale * s",
        "                _rcx = max(0.0, min(float(nw), _rcx))",
        "                _rcy = max(0.0, min(float(nh), _rcy))",
        "            else:",
        "                _rcx, _rcy = nw / 2.0, nh / 2.0",
        "            px = int((sp.x + self.stage_w / 2) * sx - _rcx)",
        "            py = int((self.stage_h / 2 - sp.y) * sy - _rcy)",
        "            max_px = sx * self.stage_w",
        "            max_py = sy * self.stage_h",
        "            if px < -nw * 2 or px > max_px + nw or py < -nh * 2 or py > max_py + nh:",
        "                continue",
        "            try:",
        "                # Apply rotation / left-right flip based on rotation_style",
        "                _rimg = img",
        "                _rs = getattr(sp, 'rotation_style', 'all around')",
        "                _dir = getattr(sp, 'direction', 90.0)",
        "                _flipped = False",
        "                _rotated = False",
"                if _rs == 'all around' and _dir != 90.0:",
"                    # PIL's expand=True ignores center=; we must rotate on a centered canvas.",
"                    # First, create a canvas large enough to hold the rotated image.",
"                    _w, _h = img.size",
"                    _angle = 90.0 - _dir",
"                    _radians = math.radians(_angle)",
"                    _cos = abs(math.cos(_radians))",
"                    _sin = abs(math.sin(_radians))",
"                    _nw = int(_w * _cos + _h * _sin) + 1",
"                    _nh = int(_w * _sin + _h * _cos) + 1",
"                    # Create padded canvas with the image centered on its rotation center.",
"                    _canvas = Image.new('RGBA', (_nw, _nh), (0, 0, 0, 0))",
"                    _cx = (_nw // 2 - int(_rcx))",
"                    _cy = (_nh // 2 - int(_rcy))",
"                    _canvas.paste(img, (_cx, _cy), img)",
"                    # Rotate around the canvas center (which aligns with the rotation center).",
"                    _rimg = _canvas.rotate(_angle, expand=False, resample=_PIL_ROTRES,",
"                                          center=(_nw / 2.0, _nh / 2.0))",
"                    # Adjust paste position to account for canvas centering.",
"                    px = int((sp.x + self.stage_w / 2) * sx) - _nw // 2",
 "                    py = int((self.stage_h / 2 - sp.y) * sy) - _nh // 2",
        "                    _rotated = True",
        "                elif _rs == 'left-right':",
        "                    _ndir = _dir % 360.0",
        "                    if 90.0 < _ndir < 270.0:",
        "                        _rimg = img.transpose(Image.FLIP_LEFT_RIGHT)",
        "                        _flipped = True",
        "                # Mirror the horizontal rotation center when flipped",
        "                _rcx_active = (nw - _rcx) if _flipped else _rcx",
        "                if not _rotated:",
        "                    _rcx_active = (nw - _rcx) if _flipped else _rcx",
        "                    px = int((sp.x + self.stage_w / 2) * sx - _rcx_active)",
        "                    py = int((self.stage_h / 2 - sp.y) * sy - _rcy)",
        "                # Apply graphic effects (ghost transparency + color hue shift)",
        "                _rimg = self._apply_effects(_rimg, getattr(sp, '_effects', {}))",
        "                if use_numpy:",
        "                    self._composite_array(_arr, _rimg, px, py)",
        "                else:",
        "                    frame.paste(_rimg, (px, py), _rimg)",
        "            except Exception as _e:",
        "                # Never let one bad sprite blank the whole frame.",
        "                if DEBUG:",
        "                    print('[render] sprite %r failed: %r' % (getattr(sp, 'name', sp), _e))",
        "            if not use_numpy:",
        "                self._draw_bubble(frame, draw, sp)",
        "",
        "        if use_numpy:",
        "            frame = Image.fromarray(_arr)",
        "            draw = ImageDraw.Draw(frame)",
        "            for sp in ordered:",
        "                if sp.visible and not sp.is_stage:",
        "                    self._draw_bubble(frame, draw, sp)",
        "",
        "        self._photo = ImageTk.PhotoImage(frame)",
        "        # Cache the fully composited frame for color-sensing.",
        "        try:",
        "            self._last_frame = _np.array(frame) if _np is not None else None",
        "        except Exception:",
        "            self._last_frame = None",
        "        if self._image_item is None:",
        '            self._image_item = self.canvas.create_image(',
        '                0, 0, anchor="nw", image=self._photo)',
        "        else:",
        "            self.canvas.itemconfig(self._image_item, image=self._photo)",
        "",
        "        fps = stats.get(\"fps\", 0)",
        "        ft = stats.get(\"frame_time\", 0) * 1000",
        "        opf = stats.get(\"ops\", 0)",
        "        op_sec = stats.get(\"ops_per_sec\", 0)",
        "        frame_n = stats.get(\"frames\", 0)",
        "        text = (f\"FPS:{fps:.0f}  Frame:{frame_n}  \"",
        "                f\"Ops:{opf}  Ops/s:{op_sec:.0f}  \"",
        "                f\"{ft:.1f}ms\")",
        "        if self._stats_item is None:",
        '            self._stats_item = self.canvas.create_text(',
        '                8, self.stage_h * self.scale + 8,',
        '                text=text, anchor="nw",',
        '                fill="#0f0", font=("Consolas", 10))',
        "        else:",
        "            self.canvas.itemconfig(self._stats_item, text=text)",
        "",
        "    def poll(self) -> bool:",
        '        """Process pending tk events. Returns False if window was closed."""',
        "        try:",
        "            self.root.update()",
        "        except tk.TclError:",
        "            self.running = False",
        "        return self.running",
        "",
    ]
    out_path = output_dir / "_display.py"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("emitted _display.py")
    return out_path


def _generate_debug_panel(output_dir, opts=None):
    """Generate debug_panel.py — a Tkinter control + inspection window.

    The panel exposes transport controls (green flag / pause / stop / restart),
    a live debug-log viewer, a z-order inspector with working drag-reorder,
    a per-sprite status grid with rich columns, show/hide controls, export /
    data-dump tools, an "open assets in explorer" button, a detail inspector
    for the selected sprite, and an extended engine-stats tab.
    """
    output_dir = Path(output_dir)
    src = r'''# Auto-generated debug / control panel
# Generator: decompile.py
"""Tkinter control + live-debug window for decompiled Scratch projects.

Launched from the generated main.py. Reads engine/sprite state every few
milliseconds via the shared Tk mainloop (no extra threads). All state is
pulled read-only from the Engine / Sprite objects; the only mutations it
performs are transport actions (green flag, pause, stop, restart) and
inspection helpers (show/hide, z-order, exports), which are real engine
controls.
"""
import os
import json
import csv
import time as _time
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
from pathlib import Path

try:
    import psutil
    _HAVE_PSUTIL = True
except Exception:
    import types
    psutil = types.ModuleType("psutil")  # stub for type checkers
    _HAVE_PSUTIL = False


class DebugPanel:
    def __init__(self, eng, disp):
        self.eng = eng
        self.disp = disp
        self.root = getattr(disp, "root", None)
        self.refresh_ms = 90
        self._last_log_idx = 0
        self._pos_hist = {}  # sprite name -> list of (x, y)
        self._pos_hist_max = 40
        self._selected = None
        self._z_drag_data = {}
        self._tree_scroll = 0.0  # remembered scroll fraction
        # smoothness: map sprite name -> tree item id (in-place updates)
        self._tree_items = {}
        self._tree_signature = None
        self._z_signature = None
        self._refresh_count = 0
        # history ring buffer (lightweight time series)
        self._history = []
        self._history_max = 600
        self._history_every = 10  # sample every N refreshes

        self.win = tk.Toplevel(self.root)
        self.win.title("Scratch Debug & Control Panel")
        self.win.geometry("900x700")
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_styles()
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build_styles(self):
        try:
            self.style = ttk.Style(self.root)
            self.style.theme_use("clam")
            self.style.configure("DBG.TFrame", background="#1e1e2e")
            self.style.configure("DBG.TLabel", background="#1e1e2e",
                                  foreground="#cdd6f4")
            self.style.configure("DBG.TLabelframe", background="#1e1e2e",
                                  foreground="#89b4fa")
            self.style.configure("DBG.TLabelframe.Label",
                                  background="#1e1e2e", foreground="#89b4fa")
            self.style.configure("DBG.TButton", background="#313244",
                                  foreground="#cdd6f4")
            self.style.map("DBG.TButton",
                           background=[("active", "#45475a")])
            self.style.configure("DBG.Tree", background="#11111b",
                                  foreground="#cdd6f4",
                                  fieldbackground="#11111b")
            self.style.configure("DBG.Tree.Heading", background="#313244",
                                 foreground="#89b4fa")
        except Exception:
            self.style = None

    def _frame(self, master, **kw):
        try:
            return ttk.Frame(master, style="DBG.TFrame", **kw)
        except Exception:
            return tk.Frame(master, bg="#1e1e2e", **kw)

    def _label(self, master, text, **kw):
        try:
            return ttk.Label(master, text=text, style="DBG.TLabel", **kw)
        except Exception:
            return tk.Label(master, text=text, bg="#1e1e2e",
                            fg="#cdd6f4", **kw)

    def _build_ui(self):
        root = self.win
        root.configure(bg="#1e1e2e")
        root.rowconfigure(1, weight=1)
        root.columnconfigure(0, weight=1)

        # ---- transport bar ------------------------------------------------
        bar = self._frame(root)
        bar.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        for c in range(10):
            bar.columnconfigure(c, weight=0)
        bar.columnconfigure(9, weight=1)
        self.b_flag = tk.Button(bar, text=u"\u23F5 Green Flag", width=14,
                                 bg="#40c057", fg="white",
                                 font=("Segoe UI", 10, "bold"),
                                 command=self._on_green_flag)
        self.b_flag.grid(row=0, column=0, padx=3)
        self.b_pause = tk.Button(bar, text=u"\u23F8 Pause", width=12,
                                  bg="#fab005", fg="black",
                                  command=self._on_pause)
        self.b_pause.grid(row=0, column=1, padx=3)
        self.b_stop = tk.Button(bar, text=u"\u23F9 Stop", width=12,
                                 bg="#fa5252", fg="white",
                                 command=self._on_stop)
        self.b_stop.grid(row=0, column=2, padx=3)
        self.b_restart = tk.Button(bar, text=u"\u27F2 Restart", width=12,
                                    bg="#7950f2", fg="white",
                                    command=self._on_restart)
        self.b_restart.grid(row=0, column=3, padx=3)
        self.b_hide_sel = tk.Button(bar, text="Hide Sel", width=10,
                                     bg="#313244", fg="#cdd6f4",
                                     command=self._on_hide_selected)
        self.b_hide_sel.grid(row=0, column=4, padx=3)
        self.b_show_sel = tk.Button(bar, text="Show Sel", width=10,
                                     bg="#313244", fg="#cdd6f4",
                                     command=self._on_show_selected)
        self.b_show_sel.grid(row=0, column=5, padx=3)
        self.b_export = tk.Button(bar, text="Export", width=10,
                                   bg="#1098ad", fg="white",
                                   command=self._on_export)
        self.b_export.grid(row=0, column=6, padx=3)
        self.b_explorer = tk.Button(bar, text="Open Folder", width=12,
                                     bg="#1098ad", fg="white",
                                     command=self._on_open_assets)
        self.b_explorer.grid(row=0, column=7, padx=3)
        self.b_logclear = tk.Button(bar, text="Clear Log", width=10,
                                     bg="#313244", fg="#cdd6f4",
                                     command=self._on_clear_log)
        self.b_logclear.grid(row=0, column=8, padx=3)
        self.b_autoscroll = tk.BooleanVar(value=True)
        self.cb_auto = tk.Checkbutton(bar, text="Auto-scroll",
                                       variable=self.b_autoscroll,
                                       bg="#1e1e2e", fg="#cdd6f4",
                                       selectcolor="#313244")
        self.cb_auto.grid(row=0, column=9, padx=6, sticky="e")

        self.status_var = tk.StringVar(value="running")
        self.status_lbl = self._label(bar, "", foreground="#a6e3a1",
                                       font=("Consolas", 10, "bold"))
        self.status_lbl.grid(row=0, column=10, sticky="e", padx=6)

        # ---- main notebook ------------------------------------------------
        nb = ttk.Notebook(root)
        nb.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        try:
            nb.configure(style="DBG.TNotebook")
        except Exception:
            pass

        f_sprites = self._frame(nb)
        nb.add(f_sprites, text="  Sprites  ")
        self._build_sprite_tab(f_sprites)

        f_z = self._frame(nb)
        nb.add(f_z, text="  Z-Order  ")
        self._build_zorder_tab(f_z)

        f_log = self._frame(nb)
        nb.add(f_log, text="  Live Log  ")
        self._build_log_tab(f_log)

        f_insp = self._frame(nb)
        nb.add(f_insp, text="  Inspector  ")
        self._build_inspector_tab(f_insp)

        f_mix = self._frame(nb)
        nb.add(f_mix, text="  Sound Mixer  ")
        self._build_mixer_tab(f_mix)

        f_stat = self._frame(nb)
        nb.add(f_stat, text="  Engine Stats  ")
        self._build_stats_tab(f_stat)

    # ---- Sprites grid ---------------------------------------------------
    def _build_sprite_tab(self, parent):
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)

        top = self._frame(parent)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._label(top, "Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._refresh_sprite_grid())
        tk.Entry(top, textvariable=self.filter_var, width=24,
                 bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4"
                 ).pack(side="left", padx=4)
        self.sprite_count_var = tk.StringVar(value="0 sprites")
        self._label(top, "", textvariable=self.sprite_count_var
                     ).pack(side="right", padx=6)

        selrow = self._frame(parent)
        selrow.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        tk.Button(selrow, text="Show", width=8, bg="#40c057", fg="white",
                  command=self._on_show_selected).pack(side="left", padx=2)
        tk.Button(selrow, text="Hide", width=8, bg="#fa5252", fg="white",
                  command=self._on_hide_selected).pack(side="left", padx=2)
        tk.Button(selrow, text="Front", width=8, bg="#fab005", fg="black",
                  command=lambda: self._on_z_nudge("front")
                  ).pack(side="left", padx=2)
        tk.Button(selrow, text="Back", width=8, bg="#fab005", fg="black",
                  command=lambda: self._on_z_nudge("back")
                  ).pack(side="left", padx=2)
        tk.Button(selrow, text="+1", width=6, bg="#313244", fg="#cdd6f4",
                  command=lambda: self._on_z_nudge("fwd")
                  ).pack(side="left", padx=2)
        tk.Button(selrow, text="-1", width=6, bg="#313244", fg="#cdd6f4",
                  command=lambda: self._on_z_nudge("backw")
                  ).pack(side="left", padx=2)

        cols = ("name", "type", "visible", "x", "y", "dir", "size",
                "costume", "costume_idx", "z", "ops", "ops_sec",
                "active", "clone", "saying", "vars", "lists", "effects",
                "rotation", "tasks", "pen")
        self.sprite_tree = ttk.Treeview(parent, columns=cols, show="headings",
                                        selectmode="browse")
        try:
            self.sprite_tree.configure(style="DBG.Tree")
        except Exception:
            pass
        widths = {"name": 150, "type": 55, "visible": 55, "x": 55, "y": 55,
                  "dir": 45, "size": 45, "costume": 130, "costume_idx": 65,
                  "z": 45, "ops": 55, "ops_sec": 65, "active": 50,
                  "clone": 45, "saying": 150, "vars": 45, "lists": 45,
                  "effects": 110, "rotation": 85, "tasks": 45, "pen": 45}
        for c in cols:
            self.sprite_tree.heading(c, text=c,
                                     command=lambda col=c: self._sort_sprites(col))
            self.sprite_tree.column(c, width=widths.get(c, 70),
                                    stretch=(c in ("name", "costume", "saying",
                                                   "effects")))
        ysc = ttk.Scrollbar(parent, orient="vertical",
                            command=self.sprite_tree.yview)
        xsc = ttk.Scrollbar(parent, orient="horizontal",
                            command=self.sprite_tree.xview)
        self.sprite_tree.configure(yscrollcommand=ysc.set,
                                   xscrollcommand=xsc.set)
        self.sprite_tree.grid(row=2, column=0, sticky="nsew")
        ysc.grid(row=2, column=1, sticky="ns")
        xsc.grid(row=3, column=0, sticky="ew")
        self.sprite_tree.bind("<<TreeviewSelect>>", self._on_sprite_select)
        self.sprite_tree.bind("<Button-3>", self._on_sprite_rightclick)
        self._sprite_sort_col = None
        self._sprite_sort_rev = False

    def _build_zorder_tab(self, parent):
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)
        self._label(parent,
                    "Draw order (top = front-most). Drag a row to reorder "
                    "live; z-index is written back to the engine."
                    ).grid(row=0, column=0, sticky="w", padx=4, pady=(0, 4))
        self.z_list = tk.Listbox(parent, bg="#313244", fg="#cdd6f4",
                                  font=("Consolas", 11), activestyle="dotbox",
                                  selectmode="single")
        self.z_list.grid(row=1, column=0, sticky="nsew")
        zysc = ttk.Scrollbar(parent, orient="vertical",
                             command=self.z_list.yview)
        self.z_list.configure(yscrollcommand=zysc.set)
        zysc.grid(row=1, column=1, sticky="ns")
        # drag-to-reorder
        self.z_list.bind("<ButtonPress-1>", self._z_on_press)
        self.z_list.bind("<B1-Motion>", self._z_on_drag)
        self.z_list.bind("<ButtonRelease-1>", self._z_on_release)
        # double-click convenience
        self.z_list.bind("<Double-Button-1>",
                         lambda e: self._z_bring_to_front())
        self._label(parent,
                    "Double-click a row to send it to the front."
                    ).grid(row=2, column=0, sticky="w", padx=4, pady=(2, 0))

    def _build_log_tab(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(
            parent, bg="#11111b", fg="#cdd6f4", font=("Consolas", 10),
            insertbackground="#cdd6f4")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state="disabled")

    def _build_inspector_tab(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.inspector_text = scrolledtext.ScrolledText(
            parent, bg="#11111b", fg="#cdd6f4", font=("Consolas", 10),
            insertbackground="#cdd6f4")
        self.inspector_text.grid(row=0, column=0, sticky="nsew")
        self.inspector_text.configure(state="disabled")

    def _build_stats_tab(self, parent):
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)
        self._label(parent, "Global engine statistics (live):"
                     ).grid(row=0, column=0, sticky="w", padx=4, pady=(0, 4))
        ctrl = self._frame(parent)
        ctrl.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 4))
        ctrl.columnconfigure(1, weight=1)
        ctrl.columnconfigure(3, weight=1)
        ctrl.columnconfigure(5, weight=1)
        self._label(ctrl, "Warp time (s):").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self._warp_time_var = tk.DoubleVar(value=0.5)
        self._warp_time_scale = tk.Scale(
            ctrl, from_=0.0, to=5.0, resolution=0.05, orient="horizontal",
            variable=self._warp_time_var, bg="#313244", fg="#cdd6f4",
            troughcolor="#45475a", highlightthickness=0, length=130)
        self._warp_time_scale.grid(row=0, column=1, sticky="ew", padx=4)
        self._warp_time_scale.bind("<ButtonRelease-1>", self._on_warp_time_changed)
        self._label(ctrl, "Iter lim:").grid(row=0, column=2, sticky="w", padx=(10, 4))
        self._warp_iter_var = tk.IntVar(value=500_000)
        self._warp_iter_scale = tk.Scale(
            ctrl, from_=1_000, to=10_000_000, resolution=1_000, orient="horizontal",
            variable=self._warp_iter_var, bg="#313244", fg="#cdd6f4",
            troughcolor="#45475a", highlightthickness=0, length=120)
        self._warp_iter_scale.grid(row=0, column=3, sticky="ew", padx=4)
        self._warp_iter_scale.bind("<ButtonRelease-1>", self._on_warp_iter_changed)
        self._label(ctrl, "Stride:").grid(row=0, column=4, sticky="w", padx=(10, 4))
        self._warp_stride_var = tk.IntVar(value=1000)
        self._warp_stride_scale = tk.Scale(
            ctrl, from_=1, to=10_000, resolution=100, orient="horizontal",
            variable=self._warp_stride_var, bg="#313244", fg="#cdd6f4",
            troughcolor="#45475a", highlightthickness=0, length=100)
        self._warp_stride_scale.grid(row=0, column=5, sticky="ew", padx=4)
        self._warp_stride_scale.bind("<ButtonRelease-1>", self._on_warp_stride_changed)
        self._label(ctrl, "(time=0 disables guard)").grid(row=1, column=0, columnspan=5, sticky="w", padx=4)
        tk.Button(ctrl, text="Reset defaults", width=14, bg="#313244", fg="#cdd6f4",
                  command=self._on_warp_reset).grid(row=1, column=5, sticky="e", padx=4)
        self.stats_text = scrolledtext.ScrolledText(
            parent, bg="#11111b", fg="#cdd6f4", font=("Consolas", 10),
            insertbackground="#cdd6f4")
        self.stats_text.grid(row=2, column=0, sticky="nsew")
        self.stats_text.configure(state="disabled")

    def _build_mixer_tab(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        top = self._frame(parent)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        tk.Button(top, text="Open Decompiled Folder", width=20,
                  bg="#1098ad", fg="white",
                  command=self._on_open_assets).pack(side="left", padx=3)
        tk.Button(top, text="Stop All Sounds", width=14,
                  bg="#fa5252", fg="white",
                  command=lambda: (getattr(self.eng, "stop_sounds", lambda: None)(),
                                   self.eng.log("stop all sounds", "CONTROL"))
                  ).pack(side="left", padx=3)
        self._label(top, "Right-click a sound to open its file."
                     ).pack(side="right", padx=6)
        cols = ("sound", "path", "playing")
        self.sound_tree = ttk.Treeview(parent, columns=cols, show="headings",
                                       selectmode="browse", height=12)
        try:
            self.sound_tree.configure(style="DBG.Tree")
        except Exception:
            pass
        for c in cols:
            self.sound_tree.heading(c, text=c)
        self.sound_tree.column("sound", width=180)
        self.sound_tree.column("path", width=360, stretch=True)
        self.sound_tree.column("playing", width=70)
        sy = ttk.Scrollbar(parent, orient="vertical",
                           command=self.sound_tree.yview)
        self.sound_tree.configure(yscrollcommand=sy.set)
        self.sound_tree.grid(row=1, column=0, sticky="nsew")
        sy.grid(row=1, column=1, sticky="ns")
        self.sound_tree.bind("<Button-3>", self._on_sound_rightclick)

    # ------------------------------------------------------------------
    # transport controls
    # ------------------------------------------------------------------
    def _on_green_flag(self):
        self.eng.paused = False
        self.eng.start(green_flag=True)
        self.eng.running = True
        if getattr(self.disp, "running", True) is False:
            self.disp.running = True
        self.eng.log("GREEN FLAG (re-clicked)", "CONTROL")

    def _on_pause(self):
        self.eng.paused = not getattr(self.eng, "paused", False)
        self.eng.stats["paused"] = self.eng.paused
        self.b_pause.configure(
            text=u"\u25B6 Resume" if self.eng.paused else u"\u23F8 Pause",
            bg="#fab005" if not self.eng.paused else "#15aabf",
            fg="black")
        self.eng.log("PAUSED" if self.eng.paused else "RESUMED", "CONTROL")

    def _on_stop(self):
        self.eng.running = False
        if hasattr(self.disp, "running"):
            self.disp.running = False
        self.eng.paused = False
        self.eng.log("STOP", "CONTROL")

    def _on_restart(self):
        self.eng.running = True
        if hasattr(self.disp, "running"):
            self.disp.running = True
        self.eng.paused = False
        self.eng.start(green_flag=True)
        self.b_pause.configure(text=u"\u23F8 Pause", bg="#fab005", fg="black")
        self.eng.log("RESTART (green flag)", "CONTROL")

    def _on_clear_log(self):
        if hasattr(self.eng, "clear_log"):
            self.eng.clear_log()
        self._last_log_idx = 0
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _on_close(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    # ---- show / hide ----------------------------------------------------
    def _sel_sprite(self):
        return self.eng.sprites.get(self._selected) if self._selected else None

    def _on_show_selected(self):
        sp = self._sel_sprite()
        if sp is not None:
            sp.visible = True
            self.eng.log(f"SHOW {sp.name!r}", "INSPECT")

    def _on_hide_selected(self):
        sp = self._sel_sprite()
        if sp is not None:
            sp.visible = False
            self.eng.log(f"HIDE {sp.name!r}", "INSPECT")

    # ---- z-order nudge --------------------------------------------------
    def _on_z_nudge(self, which):
        sp = self._sel_sprite()
        if sp is None:
            return
        others = [s for s in self.eng.sprites.values() if s is not sp]
        if which == "front":
            sp.z_index = (max((s.z_index for s in others), default=0) + 1)
        elif which == "back":
            sp.z_index = (min((s.z_index for s in others), default=0) - 1)
        elif which == "fwd":
            sp.z_index += 1
        elif which == "backw":
            sp.z_index -= 1
        self.eng.log(f"z {sp.name!r} -> {sp.z_index}", "INSPECT")

    # ------------------------------------------------------------------
    # sprite grid
    # ------------------------------------------------------------------
    def _iter_sprites(self):
        return list(self.eng.sprites.values())

    def _refresh_sprite_grid(self):
        tree = self.sprite_tree
        filt = (self.filter_var.get() if hasattr(self, "filter_var") else "").lower()
        cols = ("name", "type", "visible", "x", "y", "dir", "size",
                "costume", "costume_idx", "z", "ops", "ops_sec", "active",
                "clone", "saying", "vars", "lists", "effects", "rotation",
                "tasks", "pen")
        rows = []
        for sp in self._iter_sprites():
            if filt and filt not in sp.name.lower():
                continue
            costume_name = ""
            try:
                if sp.costumes and 0 <= sp._costume_index < len(sp.costumes):
                    costume_name = sp.costumes[sp._costume_index].get("name", "")
            except Exception:
                costume_name = ""
            saying = (getattr(sp, "_say_text", "") or "")[:40]
            eff = getattr(sp, "_effects", {})
            eff_str = ",".join(f"{k}={int(round(v))}" for k, v in eff.items()
                               if v) if eff else "-"
            try:
                _t = self.eng._task_sprite
                task_count = sum(1 for n in _t.values() if n == sp.name)
            except Exception:
                task_count = 0
            rows.append((sp.name, "stage" if sp.is_stage else "sprite",
                         "yes" if sp.visible else "no",
                         f"{sp.x:.1f}", f"{sp.y:.1f}", f"{sp.direction:.0f}",
                         f"{sp.size:.0f}", costume_name,
                         f"{sp._costume_index + 1}/{len(sp.costumes)}",
                         str(sp.z_index), str(sp._ops),
                         f"{sp._ops_per_sec:.1f}",
                         "yes" if sp._active else "no",
                         "yes" if sp._clone else "no", saying,
                         str(len(sp.vars)), str(len(sp.lists)), eff_str,
                         sp.rotation_style, str(task_count),
                         "down" if sp.pen_down else "-"))
        # full rebuild ONLY when the set of rows or sort order changes
        sig = (tuple(r[0] for r in rows),
               self._sprite_sort_col, self._sprite_sort_rev, filt)
        if sig != self._tree_signature:
            try:
                self._tree_scroll = tree.yview()[0]
            except Exception:
                pass
            tree.delete(*tree.get_children())
            self._tree_items.clear()
            if self._sprite_sort_col is not None:
                ci = cols.index(self._sprite_sort_col)
                rows.sort(key=lambda r: self._sort_key(r[ci]),
                          reverse=self._sprite_sort_rev)
            for r in rows:
                iid = tree.insert("", "end", values=r, tags=(r[0],))
                self._tree_items[r[0]] = iid
            try:
                tree.yview_moveto(self._tree_scroll)
            except Exception:
                pass
            self._tree_signature = sig
        else:
            # fast path: update values in place (keeps scroll + selection)
            for r in rows:
                iid = self._tree_items.get(r[0])
                if iid:
                    tree.item(iid, values=r)
        try:
            tree.tag_configure("yes", foreground="#a6e3a1")
        except Exception:
            pass
        self.sprite_count_var.set(f"{len(rows)} sprites")

    @staticmethod
    def _sort_key(v):
        try:
            return (0, float(v))
        except (ValueError, TypeError):
            pass
        if v in ("yes", "no", "down", "-"):
            return (1, 0 if v in ("yes", "down") else 1)
        return (2, v)

    def _sort_sprites(self, col):
        if self._sprite_sort_col == col:
            self._sprite_sort_rev = not self._sprite_sort_rev
        else:
            self._sprite_sort_col = col
            self._sprite_sort_rev = False
        self._refresh_sprite_grid()

    def _on_sprite_select(self, _ev):
        sel = self.sprite_tree.selection()
        if sel:
            self._selected = self.sprite_tree.set(sel[0], "name")
        self._refresh_inspector()

    def _on_sprite_rightclick(self, _ev):
        sel = self.sprite_tree.selection()
        if not sel:
            return
        name = self.sprite_tree.set(sel[0], "name")
        sp = self.eng.sprites.get(name)
        if sp is None:
            return
        md5 = getattr(sp, "_costume_md5", None)
        if not md5:
            return
        base = getattr(self.disp, "assets_dir", None) or "."
        path = os.path.join(str(base), "data", name, md5)
        if os.path.exists(path):
            self._open_in_explorer(path)
            self.eng.log(f"open costume {md5} for {name!r}", "INSPECT")
        else:
            self.eng.log(f"costume file not found: {path}", "INSPECT")

    def _on_sound_rightclick(self, _ev):
        sel = self.sound_tree.selection()
        if not sel:
            return
        path = self.sound_tree.set(sel[0], "path")
        if path and os.path.exists(path):
            self._open_in_explorer(path)
            self.eng.log(f"open sound {os.path.basename(path)}", "INSPECT")
        else:
            self.eng.log(f"sound file not found: {path}", "INSPECT")

    # ------------------------------------------------------------------
    # z-order (working drag reorder)
    # ------------------------------------------------------------------
    def _z_rebuild(self):
        ordered = sorted(self._iter_sprites(),
                         key=lambda s: s.z_index, reverse=True)
        sig = tuple((sp.name, sp.z_index, sp.is_stage, sp._clone) for sp in ordered)
        if sig == self._z_signature:
            return  # unchanged -> don't touch the widget (keeps scroll/selection)
        self._z_signature = sig
        self.z_list.delete(0, "end")
        for sp in ordered:
            tag = " [stage]" if sp.is_stage else ""
            cl = " [clone]" if sp._clone else ""
            self.z_list.insert("end", f"z={sp.z_index:>4}  {sp.name}{tag}{cl}")

    def _z_bring_to_front(self):
        sel = self.z_list.curselection()
        if not sel:
            return
        items = self.z_list.get(0, "end")
        line = items[sel[0]]
        name = line.split("  ", 1)[1].split(" [")[0]
        sp = self.eng.sprites.get(name)
        if sp is not None:
            others = [s for s in self.eng.sprites.values() if s is not sp]
            sp.z_index = (max((s.z_index for s in others), default=0) + 1)
            self.eng.log(f"z front {sp.name!r}", "INSPECT")
        self._z_rebuild()

    def _z_on_press(self, ev):
        self._z_drag_data["idx"] = self.z_list.nearest(ev.y)
        self._z_drag_data["y"] = ev.y

    def _z_on_drag(self, ev):
        if "idx" not in self._z_drag_data:
            return
        src = self._z_drag_data["idx"]
        dst = self.z_list.nearest(ev.y)
        if dst == src or dst < 0 or dst >= self.z_list.size():
            return
        # swap the two listbox items visually during drag
        items = list(self.z_list.get(0, "end"))
        if 0 <= src < len(items):
            items[src], items[dst] = items[dst], items[src]
            self.z_list.delete(0, "end")
            for it in items:
                self.z_list.insert("end", it)
            self.z_list.selection_clear(0, "end")
            self.z_list.selection_set(dst)
            self.z_list.see(dst)
            self._z_drag_data["idx"] = dst

    def _z_on_release(self, ev):
        if "idx" not in self._z_drag_data:
            return
        items = self.z_list.get(0, "end")
        n = len(items)
        for i, line in enumerate(items):
            name = line.split("  ", 1)[1].split(" [")[0]
            sp = self.eng.sprites.get(name)
            if sp is not None:
                sp.z_index = n - 1 - i  # top of list == front
        self._z_drag_data.clear()
        self.eng.log("z-order re-ordered by drag", "INSPECT")

    # ------------------------------------------------------------------
    # inspector
    # ------------------------------------------------------------------
    def _refresh_inspector(self):
        sp = self._sel_sprite()
        txt = self.inspector_text
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        if sp is None:
            txt.insert("end", "(select a sprite from the Sprites tab)")
            txt.configure(state="disabled")
            return
        L = []
        L.append(f"=== {sp.name} ===")
        L.append(f"type         : {'STAGE' if sp.is_stage else 'sprite'}")
        L.append(f"visible      : {sp.visible}")
        L.append(f"position     : x={sp.x:.2f}  y={sp.y:.2f}")
        L.append(f"direction    : {sp.direction:.1f}")
        L.append(f"size         : {sp.size:.1f}%")
        L.append(f"rotation     : {sp.rotation_style}")
        L.append(f"z-index      : {sp.z_index}")
        L.append(f"clone        : {sp._clone}")
        L.append(f"ops total    : {sp._ops}")
        L.append(f"ops/sec      : {sp._ops_per_sec:.1f}")
        L.append(f"active       : {sp._active}")
        L.append(f"say text     : {getattr(sp, '_say_text', '')!r}")
        L.append(f"pen down     : {sp.pen_down}")
        L.append(f"spawn frame  : {sp._spawn_frame}")
        L.append(f"last active  : frame {sp._last_active_frame}")
        L.append("")
        L.append("-- costumes --")
        for i, c in enumerate(sp.costumes):
            mark = " >" if i == sp._costume_index else "  "
            L.append(f"{mark} {i + 1}. {c.get('name', '?')}  "
                     f"({c.get('md5ext', '')})")
        L.append("")
        L.append("-- graphic effects --")
        for k, v in sp._effects.items():
            L.append(f"   {k:>10} = {v}")
        L.append("")
        L.append("-- variables --")
        for k, v in sp.vars.items():
            L.append(f"   {k} = {v!r}")
        if sp._stage is not None:
            for k, v in sp._stage.vars.items():
                if k not in sp.vars:
                    L.append(f"   {k} (stage) = {v!r}")
        L.append("")
        L.append("-- lists --")
        for k, v in sp.lists.items():
            L.append(f"   {k} ({len(v)} items) = {v!r}")
        L.append("")
        L.append("-- procedures --")
        for pname in sp._procs.keys():
            L.append(f"   {pname}")
        L.append("")
        L.append("-- position history (last 12) --")
        hist = self._pos_hist.get(sp.name, [])
        for (hx, hy) in hist[-12:]:
            L.append(f"   x={hx:.1f} y={hy:.1f}")
        txt.insert("end", "\n".join(L))
        txt.configure(state="disabled")

    # ------------------------------------------------------------------
    # export / data dump / assets
    # ------------------------------------------------------------------
    @staticmethod
    def _trunc(v, maxlen=120):
        """Summarize a value so huge blobs (textures, long lists) stay small."""
        s = repr(v)
        if len(s) > maxlen:
            return s[:maxlen] + f"...<{len(s) - maxlen} more chars>"
        return s

    @staticmethod
    def _summarize_list(v, max_items=3):
        if not isinstance(v, (list, tuple)):
            return DebugPanel._trunc(v)
        if len(v) <= max_items:
            return "[" + ", ".join(DebugPanel._trunc(x, 60) for x in v) + "]"
        head = ", ".join(DebugPanel._trunc(x, 60) for x in v[:max_items])
        return f"[{head}, ... <{len(v) - max_items} more items>]"

    def _snapshot(self, opts):
        """Build a snapshot. opts is a dict of toggle flags (granular)."""
        shot = {
            "frame": self.eng._frame,
            "time": _time.time(),
            "paused": getattr(self.eng, "paused", False),
            "running": getattr(self.eng, "running", True),
            "stats": dict(self.eng.stats),
        }
        if opts.get("history"):
            shot["history"] = list(self._history)
        shot["sprites"] = []
        for sp in self._iter_sprites():
            costume_name = ""
            try:
                if sp.costumes and 0 <= sp._costume_index < len(sp.costumes):
                    costume_name = sp.costumes[sp._costume_index].get("name", "")
            except Exception:
                pass
            row = {
                "name": sp.name,
                "type": "stage" if sp.is_stage else "sprite",
                "visible": sp.visible,
                "x": sp.x, "y": sp.y,
                "direction": sp.direction,
                "size": sp.size,
                "rotation_style": sp.rotation_style,
                "z_index": sp.z_index,
                "clone": sp._clone,
                "ops": sp._ops,
                "ops_per_sec": round(sp._ops_per_sec, 2),
                "active": sp._active,
                "costume": costume_name,
                "costume_index": sp._costume_index,
            }
            if opts.get("effects"):
                row["effects"] = {k: v for k, v in sp._effects.items() if v}
            if opts.get("pen"):
                row["pen_down"] = sp.pen_down
                row["say_text"] = getattr(sp, "_say_text", "")
            if opts.get("costumes"):
                row["costumes"] = [
                    {"name": c.get("name", ""),
                     "md5ext": c.get("md5ext", "")} for c in sp.costumes]
            if opts.get("vars"):
                # smart: only counts by default; full values stay truncated
                row["vars"] = {k: self._trunc(v) for k, v in sp.vars.items()}
            else:
                row["var_count"] = len(sp.vars)
            if opts.get("lists"):
                row["lists"] = {k: self._summarize_list(v)
                                for k, v in sp.lists.items()}
            else:
                row["list_count"] = len(sp.lists)
            if opts.get("procs"):
                row["procedures"] = list(sp._procs.keys())
            shot["sprites"].append(row)
        return shot

    def _on_export(self):
        # options dialog (granular + smart defaults)
        win = tk.Toplevel(self.win)
        win.title("Export options")
        win.configure(bg="#1e1e2e")
        defaults = {
            "grid": True, "stats": True, "effects": True, "pen": True,
            "costumes": False, "vars": False, "lists": False,
            "procs": False, "history": False, "log": False,
        }
        vars_d = {}
        desc = {
            "grid": "Compact CSV grid (x,y,z,visible,ops…) — tiny",
            "stats": "Engine stats block",
            "effects": "Graphic effects",
            "pen": "Pen-down + say-text",
            "costumes": "Costume name+md5 list",
            "vars": "Variables (TRUNCATED — no blobs)",
            "lists": "Lists (SUMMARIZED — no full textures)",
            "procs": "Procedure names",
            "history": "Historical time-series (sampled)",
            "log": "Include full debug log",
        }
        row = 0
        for key, dflt in defaults.items():
            bv = tk.BooleanVar(value=dflt)
            vars_d[key] = bv
            tk.Checkbutton(win, text=f"{key:8} — {desc[key]}",
                           variable=bv, bg="#1e1e2e", fg="#cdd6f4",
                           selectcolor="#313244", anchor="w",
                           justify="left").grid(row=row, column=0,
                                                sticky="w", padx=6, pady=1)
            row += 1

        def do_export():
            opts = {k: vars_d[k].get() for k in vars_d}
            win.destroy()
            self._do_export_with_opts(opts)
        tk.Button(win, text="Export JSON (granular)", width=24,
                  bg="#1098ad", fg="white",
                  command=do_export).grid(row=row, column=0, pady=8)
        row += 1
        tk.Button(win, text="Export CSV grid", width=24,
                  bg="#313244", fg="#cdd6f4",
                  command=lambda: (win.destroy(),
                                   self._export_csv_pick())).grid(row=row,
                                                                  column=0,
                                                                  pady=2)
        row += 1
        tk.Button(win, text="Export Log as .txt", width=24,
                  bg="#313244", fg="#cdd6f4",
                  command=lambda: (win.destroy(),
                                   self._export_log_pick())).grid(row=row,
                                                                   column=0,
                                                                   pady=2)

    def _do_export_with_opts(self, opts):
        try:
            initial = f"scratch_dbg_frame{self.eng._frame}.json"
            path = filedialog.asksaveasfilename(
                title="Export debug snapshot (granular)",
                initialfile=initial, defaultextension=".json",
                filetypes=[("JSON snapshot", "*.json"), ("All", "*.*")])
            if not path:
                return
            shot = self._snapshot(opts)
            if opts.get("log"):
                shot["log"] = [
                    {"t": e["t"], "frame": e["frame"], "level": e["level"],
                     "msg": e["msg"]}
                    for e in getattr(self.eng, "_log_entries", [])]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(shot, f, indent=1, default=str)
            size = os.path.getsize(path)
            self.eng.log(f"export -> {os.path.basename(path)} "
                         f"({size/1024.0:.1f} KB)", "EXPORT")
            try:
                messagebox.showinfo("Exported",
                                    f"Wrote {os.path.basename(path)}\n"
                                    f"Size: {size/1024.0:.1f} KB")
            except Exception:
                pass
        except Exception as _e:
            try:
                messagebox.showerror("Export failed", str(_e))
            except Exception:
                pass

    def _export_csv_pick(self):
        path = filedialog.asksaveasfilename(
            title="Export grid CSV", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")])
        if path:
            self._export_csv(path)
            self.eng.log(f"export csv -> {os.path.basename(path)}", "EXPORT")

    def _export_log_pick(self):
        path = filedialog.asksaveasfilename(
            title="Export log", defaultextension=".txt",
            filetypes=[("Text", "*.txt")])
        if path:
            self._export_log(path)
            self.eng.log(f"export log -> {os.path.basename(path)}", "EXPORT")

    def _export_csv(self, path):
        cols = ["name", "type", "visible", "x", "y", "direction", "size",
                "costume", "z_index", "ops", "ops_per_sec", "active",
                "clone", "pen_down", "vars", "lists", "procedures"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for sp in self._iter_sprites():
                costume_name = ""
                try:
                    if sp.costumes and 0 <= sp._costume_index < len(sp.costumes):
                        costume_name = sp.costumes[sp._costume_index].get("name", "")
                except Exception:
                    pass
                w.writerow([
                    sp.name, "stage" if sp.is_stage else "sprite",
                    sp.visible, round(sp.x, 2), round(sp.y, 2),
                    round(sp.direction, 1), round(sp.size, 1),
                    costume_name, sp.z_index, sp._ops, round(sp._ops_per_sec, 2),
                    sp._active, sp._clone, sp.pen_down,
                    len(sp.vars), len(sp.lists), len(sp._procs),
                ])

    def _export_log(self, path):
        entries = getattr(self.eng, "_log_entries", [])
        with open(path, "w", encoding="utf-8") as f:
            for e in entries:
                ts = _time.strftime("%H:%M:%S", _time.localtime(e["t"]))
                f.write(f"[{ts}][f{e['frame']}][{e['level']}] {e['msg']}\n")

    def _on_open_assets(self):
        try:
            # open the decompiled directory (where main.py lives)
            base = getattr(self.disp, "assets_dir", None)
            if base is None:
                base = Path(__file__).resolve().parent
            target = Path(base).resolve()
            self._open_in_explorer(str(target))
            self.eng.log(f"open folder: {target}", "EXPORT")
        except Exception as _e:
            try:
                messagebox.showerror("Open failed", str(_e))
            except Exception:
                pass

    @staticmethod
    def _open_in_explorer(path):
        import subprocess, platform
        s = platform.system()
        if s == "Windows":
            os.startfile(path)
        elif s == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _refresh_mixer(self):
        tree = self.sound_tree
        try:
            sig = tuple(sorted(self.eng._sounds.items()))
        except Exception:
            sig = None
        if sig == getattr(self, "_sound_sig", None):
            return
        self._sound_sig = sig
        tree.delete(*tree.get_children())
        for name, path in sorted(self.eng._sounds.items()):
            tree.insert("", "end", values=(name, path, "-"))

    # ------------------------------------------------------------------
    # log + stats
    # ------------------------------------------------------------------
    def _refresh_log(self):
        _raw = getattr(self.eng, "_log_entries", [])
        # _log_entries is a deque (no slice support); copy to a list first.
        entries = list(_raw)
        # Clamp the marker in case the deque (maxlen) evicted earlier entries.
        if self._last_log_idx > len(entries):
            self._last_log_idx = 0
        txt = self.log_text
        txt.configure(state="normal")
        if self._last_log_idx == 0:
            txt.delete("1.0", "end")
        new = entries[self._last_log_idx:]
        for e in new:
            ts = _time.strftime("%H:%M:%S", _time.localtime(e["t"]))
            line = f"[{ts}][f{e['frame']}][{e['level']}] {e['msg']}\n"
            txt.insert("end", line)
        if new:
            self._last_log_idx = len(entries)
            if self.b_autoscroll.get():
                txt.see("end")
        try:
            nlines = int(txt.index("end-1c").split(".")[0])
            if nlines > 4000:
                txt.delete("1.0", f"{nlines - 4000}.0")
        except Exception:
            pass
        txt.configure(state="disabled")

    def _refresh_stats(self):
        s = self.eng.stats
        txt = self.stats_text
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        L = []
        L.append(f"frames         : {s.get('frames', 0)}")
        L.append(f"fps            : {s.get('fps', 0):.1f}")
        L.append(f"ops (total)    : {s.get('ops', 0)}")
        L.append(f"ops/sec        : {s.get('ops_per_sec', 0):.1f}")
        L.append(f"tasks (queued) : {s.get('tasks', 0)}")
        L.append(f"clones         : {s.get('clones', 0)}")
        L.append(f"broadcasts     : {s.get('broadcasts', 0)}")
        L.append(f"active sprites : {sum(1 for sp in self.eng.sprites.values() if sp._active)}")
        L.append(f"visible sprites: {sum(1 for sp in self.eng.sprites.values() if sp.visible)}")
        L.append(f"hidden sprites : {sum(1 for sp in self.eng.sprites.values() if not sp.visible)}")
        L.append(f"paused         : {s.get('paused', False)}")
        L.append(f"running        : {getattr(self.eng, 'running', True)}")
        L.append(f"frame_time ms  : {s.get('frame_time', 0) * 1000:.2f}")
        L.append(f"logic_time ms  : {s.get('logic_time', 0) * 1000:.2f}")
        L.append(f"render_time ms : {s.get('render_time', 0) * 1000:.2f}")
        L.append(f"idle_time ms   : {max(0.0, (s.get('frame_time', 0) - s.get('logic_time', 0) - s.get('render_time', 0)) * 1000):.2f}")
        L.append(f"avg frame ms   : {(s.get('frame_time', 0) * 1000.0) / max(1, s.get('frames', 1)):.3f}")
        L.append(f"target_fps     : {self.eng.target_fps}")
        L.append(f"warp time lim  : {getattr(self.eng, 'warp_time_limit', 0.5):.2f}s (0=off)")
        L.append(f"warp iter lim  : {getattr(self.eng, '_warp_iter_limit', 500_000):,}")
        L.append(f"warp chk stride: {getattr(self.eng, '_warp_check_stride', 1000):,}")
        L.append(f"sprite count   : {len(self.eng.sprites)}")
        L.append(f"uptime s       : {_time.time() - getattr(self.eng, '_timer_start', _time.time()):.1f}")
        if _HAVE_PSUTIL:
            try:
                proc = psutil.Process()
                L.append(f"cpu %%          : {proc.cpu_percent():.1f}")
                L.append(f"rss memory MB  : {proc.memory_info().rss / 1e6:.1f}")
                L.append(f"threads        : {proc.num_threads()}")
            except Exception:
                pass
        txt.insert("end", "\n".join(L))
        txt.configure(state="disabled")

    # ------------------------------------------------------------------
    # refresh loop
    # ------------------------------------------------------------------
    # ---- warp limit controls ----
    def _on_warp_time_changed(self, ev=None):
        try:
            self.eng.warp_time_limit = self._warp_time_var.get()
            self.eng.log(f"warp time limit -> {self.eng.warp_time_limit:.2f}s", "CONFIG")
        except Exception:
            pass

    def _on_warp_iter_changed(self, ev=None):
        try:
            self.eng._warp_iter_limit = self._warp_iter_var.get()
            self.eng.log(f"warp iter limit -> {self.eng._warp_iter_limit:,}", "CONFIG")
        except Exception:
            pass

    def _on_warp_stride_changed(self, ev=None):
        try:
            self.eng._warp_check_stride = self._warp_stride_var.get()
            self.eng.log(f"warp check stride -> {self.eng._warp_check_stride}", "CONFIG")
        except Exception:
            pass

    def _on_warp_reset(self):
        try:
            self._warp_time_var.set(0.5)
            self._warp_iter_var.set(500_000)
            self._warp_stride_var.set(1000)
            self.eng.warp_time_limit = 0.5
            self.eng._warp_iter_limit = 500_000
            self.eng._warp_check_stride = 1000
            self.eng.log("warp limits reset to defaults (0.5s / 500K / 1000)", "CONFIG")
        except Exception:
            pass

    def _refresh(self):
        try:
            self._refresh_count += 1
            if getattr(self.disp, "running", True) and getattr(self.eng, "running", True):
                state = "PAUSED" if getattr(self.eng, "paused", False) else "RUNNING"
            else:
                state = "STOPPED"
            # light, every-tick updates (cheap, scroll-preserving)
            self.status_lbl.configure(text=f"engine: {state}   frame "
                                    f"{getattr(self.eng, '_frame', 0)}")
            self._refresh_sprite_grid()
            self._z_rebuild()
            if self._refresh_count == 1:
                try:
                    self._warp_time_var.set(getattr(self.eng, 'warp_time_limit', 0.5))
                    self._warp_iter_var.set(getattr(self.eng, '_warp_iter_limit', 500_000))
                    self._warp_stride_var.set(getattr(self.eng, '_warp_check_stride', 1000))
                except Exception:
                    pass
            # record a lightweight history sample
            if self._refresh_count % self._history_every == 0:
                sample = {"frame": self.eng._frame,
                          "t": _time.time(),
                          "sprites": [
                              {"name": sp.name, "x": round(sp.x, 2),
                               "y": round(sp.y, 2), "z": sp.z_index,
                               "visible": sp.visible, "ops": sp._ops,
                               "active": sp._active}
                              for sp in self._iter_sprites()]}
                self._history.append(sample)
                if len(self._history) > self._history_max:
                    del self._history[0:]
            # heavy text redraws throttled (keeps the UI silky)
            if self._refresh_count % 5 == 0:
                self._refresh_log()
                self._refresh_stats()
                self._refresh_mixer()
            # inspector only when a sprite is selected (and throttled)
            if self._selected and self._refresh_count % 5 == 0:
                self._refresh_inspector()
            for sp in self._iter_sprites():
                h = self._pos_hist.setdefault(sp.name, [])
                h.append((sp.x, sp.y))
                if len(h) > self._pos_hist_max:
                    del h[0:]
        except Exception:
            pass
        try:
            self.win.after(self.refresh_ms, self._refresh)
        except Exception:
            pass
'''
    out_path = output_dir / "debug_panel.py"
    out_path.write_text(src, encoding="utf-8")
    log.info("emitted debug_panel.py")
    return out_path


def _py_repr(obj):
    """Serialize a JSON-like value as valid Python source (None/True/False
    instead of JSON's null/true/false)."""
    if isinstance(obj, dict):
        return "{" + ", ".join(f"{_py_repr(k)}: {_py_repr(v)}" for k, v in obj.items()) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ", ".join(_py_repr(v) for v in obj) + "]"
    if obj is None:
        return "None"
    if obj is True:
        return "True"
    if obj is False:
        return "False"
    if isinstance(obj, str):
        return repr(obj)
    return repr(obj)


def _generate_main(ir_data, output_dir, generated, opts=None):
    """Generate a self-contained main.py entry point."""
    if opts is None:
        opts = {}
    debug = opts.get("debug", True)
    stage_w = opts.get("stage_w", 480)
    stage_h = opts.get("stage_h", 360)
    scale = opts.get("scale", 2)
    target_fps = opts.get("target_fps", 60.0)
    output_dir = Path(output_dir)
    items = []
    for name, fname in generated:
        mod = fname[:-3]
        if mod.endswith("/__init__"):
            mod = mod[: -len("/__init__")]
        items.append((name, mod))

    _costume_data = {}
    _sound_data = {}
    _z_order = []
    for t in ir_data.get("targets", []):
        sn = t.get("name", "")
        _z_order.append(sn)
        _c = [dict(c) for c in t.get("costumes", []) if c.get("md5ext")]
        if _c:
            _costume_data[sn] = _c
        _s = [dict(s) for s in t.get("sounds", []) if s.get("md5ext")]
        if _s:
            _sound_data[sn] = _s

    lines = [
        "# Auto-generated main entry point",
        "# Generator: decompile.py",
        "import argparse",
        "import importlib, sys, time",
        "from pathlib import Path",
        "from _engine import Engine, Sprite",
        "from _display import Display",
        "try:",
        "    from debug_panel import DebugPanel",
        "except Exception:",
        "    DebugPanel = None",
        "",
        "",
        "def _parse_args():",
        '    """Parse CLI flags for runtime configuration."""',
        "    parser = argparse.ArgumentParser(description='Run decompiled Scratch project')",
        "    parser.add_argument('--debug', action='store_true', default=" + repr(debug) + ",",
        "                        help='Enable debug logging')",
        "    parser.add_argument('--no-debug', action='store_false', dest='debug',",
        "                        help='Disable debug logging')",
        "    parser.add_argument('--fps', type=float, default=" + repr(target_fps) + ",",
        "                        dest='target_fps', help='Target frames per second (default: " + str(target_fps) + ")')",
        "    parser.add_argument('--scale', type=int, default=" + repr(scale) + ",",
        "                        help='Display scale factor (default: " + str(scale) + ")')",
        "    parser.add_argument('--stage-w', type=int, default=" + repr(stage_w) + ",",
        "                        dest='stage_w', help='Stage width in Scratch units (default: " + str(stage_w) + ")')",
        "    parser.add_argument('--stage-h', type=int, default=" + repr(stage_h) + ",",
        "                        dest='stage_h', help='Stage height in Scratch units (default: " + str(stage_h) + ")')",
        "    return parser.parse_args()",
        "",
        "",
        "def main():",
        '    """Load decompiled modules and run the game."""',
        "    _args = _parse_args()",
        '    _basedir = Path(__file__).resolve().parent',
        '    assets_dir = _basedir.resolve()',
        "",
        "    # create engine and display",
        "    eng = Engine()",
        "    eng.target_fps = _args.target_fps",
        '    _title = _basedir.name or "Scratch"',
        "    disp = Display(title=_title, stage_size=(_args.stage_w, _args.stage_h), scale=_args.scale,",
        "                  assets_dir=assets_dir)",
        "    eng.display = disp",
        "    disp.eng = eng",
        "",
        "    # register all sprites",
    ]
    # use importlib because module names start with digits
    # Import+register Stage first, flush display, then import the rest
    # so the user sees a window before the remaining modules load.
    if items:
        lines.append(f"    reg0 = importlib.import_module({items[0][1]!r}).register")
        lines.append("    reg0(eng)")
        lines.append("    disp.root.update_idletasks()  # first paint before remaining imports")
    for i, (name, mod) in enumerate(items[1:], 1):
        lines.append(f"    reg{i} = importlib.import_module({mod!r}).register")
        lines.append(f"    reg{i}(eng)")
    lines += [
        "",
        "    # ---- asset + z-index data (loaded from _asset_data.json)",
        "    # to avoid recompiling a huge inline dict literal every launch) ----",
        "    import json as _json",
        "    with open(_basedir / '_asset_data.json', 'r', encoding='utf-8') as _f:",
        "        _asset_data = _json.load(_f)",
        "    _costume_data = _asset_data['costumes']",
        "    for _sn, _cdl in _costume_data.items():",
        "        if _sn in eng.sprites:",
        "            eng.sprites[_sn].load_costumes(_cdl)",
        "",
        "    _z_order = _asset_data.get('z_order', [])",
        "    for _zi, _zn in enumerate(_z_order):",
        "        if _zn in eng.sprites:",
        "            eng.sprites[_zn].z_index = _zi",
        "",
        "    _sound_data = _asset_data.get('sounds', {})",
        "    if _sound_data:",
        "        eng.load_sounds(_sound_data)",
        "",
        "    # start the engine",
        "    eng.start()",
        "",
        "    # launch the debug / control panel (only when debug is enabled)",
        "    _dbg = None",
        "    if _args.debug and DebugPanel is not None:",
        "        try:",
        "            _dbg = DebugPanel(eng, disp)",
        "            # Position debug panel to the right of the main window (deferred)",
        "            def _place_debug_panel():",
            "                try:",
            "                    _dbg.win.update_idletasks()",
            "                    _pw = _dbg.win.winfo_reqwidth() or 900",
            "                    _ph = _dbg.win.winfo_reqheight() or 700",
            "                    _sw = _dbg.win.winfo_screenwidth()",
            "                    _sh = _dbg.win.winfo_screenheight()",
            "                    _rx = disp.root.winfo_rootx()",
            "                    _ry = disp.root.winfo_rooty()",
            "                    _rw = disp.root.winfo_width()",
            "                    _rh = disp.root.winfo_height()",
            "                    # Try right of main window first",
            "                    _px = _rx + _rw + 10",
            "                    _py = _ry",
            "                    # If panel extends past right screen edge, try left",
            "                    if _px + _pw > _sw:",
            "                        _px = max(0, _rx - _pw - 10)",
            "                    # If past bottom, clamp to screen",
            "                    if _py + _ph > _sh:",
            "                        _py = max(0, _sh - _ph)",
            "                    # If main is too wide (panel would overlap), place below",
            "                    if _px < _rx + _rw and _px + _pw > _rx:",
            "                        _px = _rx",
            "                        _py = _ry + _rh + 10",
            "                        if _py + _ph > _sh:",
            "                            _py = max(0, _ry - _ph - 10)",
            "                    _dbg.win.geometry(f'+{_px}+{_py}')",
            "                    _dbg.win.attributes('-topmost', False)",
            "                    disp.root.lift()",
            "                    disp.root.focus_force()",
            "                except Exception:",
            "                    pass",
        "            disp.root.after(200, _place_debug_panel)",
        "        except Exception as _e:",
        "            if _args.debug:",
        "                import traceback; traceback.print_exc()",
         "",
    "    # On Windows, raise the system timer resolution to 1ms so Tk's",
    "    # after()/perf_counter pacing isn't quantised to the default ~15.6ms",
    "    # tick, which otherwise causes visible frame jitter.",
    "    _timer_hi = False",
    "    try:",
    "        import ctypes, sys as _sys",
    "        if _sys.platform == 'win32':",
    "            ctypes.windll.winmm.timeBeginPeriod(1)",
    "            _timer_hi = True",
    "            import atexit as _atexit",
    "            def _release_timer():",
    "                try: ctypes.windll.winmm.timeEndPeriod(1)",
    "                except Exception: pass",
    "            _atexit.register(_release_timer)",
    "    except Exception:",
    "        _timer_hi = False",
         "",
         "    # game loop (phase-locked to a fixed schedule). We poll at high",
         "    # resolution and only run a frame once perf_counter reaches the next",
         "    # scheduled time, decoupling frame pacing from Tk's coarse timer.",
         "    _loop_start = time.perf_counter()",
         "    _next_t = _loop_start",
         "    def tick():",
         "        nonlocal _next_t",
         "        try:",
         "            if not disp.running:",
         "                if _timer_hi:",
         "                    try:",
         "                        ctypes.windll.winmm.timeEndPeriod(1)",
         "                    except Exception:",
         "                        pass",
         "                return",
         "            # Not time for the next frame yet: re-poll very soon.",
         "            _now0 = time.perf_counter()",
         "            if _now0 < _next_t:",
         "                _sleep = _next_t - _now0",
         "                if _sleep > 0.003:",
         "                    disp.root.after(int((_sleep - 0.002) * 1000), tick)",
         "                else:",
         "                    disp.root.after(1, tick)",
         "                return",
         "            t_start = time.perf_counter()",
        "            eng.set_input(disp.key_state, disp.mouse_state)",
        "            eng.run_frame()",
        "            t_logic = time.perf_counter()",
        "            disp.render(eng.sprites, eng.stats)",
        "            t_render = time.perf_counter()",
        "            elapsed = t_render - t_start",
        "            actual_dt = t_start - _next_t + (1.0 / eng.target_fps)",
        "            eng.stats[\"frame_time\"] = elapsed",
        "            eng.stats[\"logic_time\"] = t_logic - t_start",
        "            eng.stats[\"render_time\"] = t_render - t_logic",
        "            _inst_fps = 1.0 / max(0.001, actual_dt) if actual_dt > 0 else 0",
            "            eng.stats[\"fps\"] = eng.stats.get(\"fps\", _inst_fps) * 0.7 + _inst_fps * 0.3",
        "            if eng._frame % 30 == 0:",
        "                dt = time.perf_counter() - _loop_start",
        "                if dt > 0:",
         "                    eng.stats[\"ops_per_sec\"] = eng.stats[\"ops\"] / dt",
         "                if _args.debug:",
         "                    print(f\"[MAIN] frame {eng._frame} fps={eng.stats['fps']:.1f} ops={eng.stats['ops']}\", flush=True)",
        "            if not eng.running:",
        "                disp.running = False",
        "                return",
         "            target_dt = 1.0 / eng.target_fps",
         "            _next_t += target_dt",
         "            now = time.perf_counter()",
         "            if _next_t < now:",
         "                # Fell behind: resync and count the dropped frame.",
         "                eng.stats['dropped_frames'] = eng.stats.get('dropped_frames', 0) + 1",
         "                _next_t = now + target_dt",
         "            # Re-poll: sleep most of the remaining time via Tk, then let",
         "            # the poll branch above fine-tune with 1ms wakeups.",
         "            _rem = _next_t - now",
         "            if _rem > 0.003:",
         "                disp.root.after(int((_rem - 0.002) * 1000), tick)",
         "            else:",
         "                disp.root.after(1, tick)",
         "        except Exception as _e:",
         "            import traceback",
         "            traceback.print_exc()",
         "            disp.running = False",
        "",
        "    eng.running = True",
        "    disp.root.after(0, tick)",
        "    disp.root.mainloop()",
        "",
        "",
        'if __name__ == "__main__":',
        "    main()",
        "",
    ]
    # Write asset data to a separate JSON file (avoids recompiling a huge
    # inline dict literal every launch).
    import json as _json_mod
    asset_path = output_dir / "_asset_data.json"
    asset_path.write_text(_json_mod.dumps({
        "costumes": _costume_data,
        "sounds": _sound_data,
        "z_order": _z_order,
    }, ensure_ascii=False, separators=(',', ':')), encoding="utf-8")
    log.info("emitted _asset_data.json")
    out_path = output_dir / "main.py"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("emitted main.py")

    # Emit a requirements.txt for the generated project's runtime dependencies.
    _req_lines = """# Runtime dependencies for the decompiled Scratch project.
# Install with: pip install -r requirements.txt
numpy>=1.24.0
sounddevice>=0.4.6
"""
    (output_dir / "requirements.txt").write_text(_req_lines, encoding="utf-8")
    log.info("emitted requirements.txt")
    return out_path


def _validate_generated(output_dir, ir_data=None):
    """Check all .py files in output_dir compile correctly.
    If ir_data is provided, also do cross-reference and asset validation,
    and optionally a headless runtime smoke test.
    """
    output_dir = Path(output_dir)
    errors = []
    import py_compile

    # 1. Syntax check - all .py files recursively
    for f in sorted(output_dir.rglob("*.py")):
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            errors.append((str(f.relative_to(output_dir)), f"Syntax: {e}"))

    if ir_data is None:
        return errors

    # 2. Cross-reference validation
    errors.extend(_validate_crossrefs(output_dir, ir_data))

    # 3. Asset existence check
    errors.extend(_validate_assets(output_dir, ir_data))

    # 4. Headless runtime smoke test (import + register + run 1 frame)
    errors.extend(_validate_runtime(output_dir, ir_data))

    return errors


def _validate_crossrefs(output_dir, ir_data):
    """Verify broadcasts, procedures, lists, variables referenced in code exist."""
    errors = []
    # Build global name sets from IR
    all_broadcasts = set()
    all_procs = {}  # proc_name -> target_name
    all_lists = {}  # (target_name, list_name) -> True
    all_vars = {}   # (target_name, var_name) -> True

    for t in ir_data.get("targets", []):
        tname = t.get("name", "")
        for bid, bname in t.get("broadcasts", {}).items():
            all_broadcasts.add(bname)
        for p in t.get("procedures", []):
            all_procs[p["name"]] = tname
        for lname in t.get("lists", {}):
            all_lists[(tname, lname)] = True
        for vname in t.get("variables", {}):
            all_vars[(tname, vname)] = True

    # Scan generated code for references
    for py_file in output_dir.rglob("*.py"):
        src = py_file.read_text(encoding="utf-8")
        # Parse the AST to get actual string values (handles \u escapes)
        import ast
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            errors.append((str(py_file.relative_to(output_dir)),
                           f"SyntaxError: {e}"))
            continue
        call_proc_strings = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "call_proc" and node.args):
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                    call_proc_strings.append(first_arg.value)

        # Check procedure calls: sp.call_proc('name', ...)
        for pname in call_proc_strings:
            # Skip comment-like calls (// or zero-width spaces) - these are no-ops at runtime
            if pname.startswith("//") or "\u200b" in pname:
                continue
            if pname not in all_procs:
                errors.append((str(py_file.relative_to(output_dir)),
                               f"Calls unknown procedure {pname!r}"))
        # NOTE: broadcast/list/variable crossref validation is intentionally
        # deferred — these require target-scoped resolution that is not
        # available at this point in the pipeline.

    return errors


def _validate_assets(output_dir, ir_data):
    """Verify every costume/sound md5ext in IR has a file in data/."""
    errors = []
    data_dir = output_dir / "data"
    for t in ir_data.get("targets", []):
        tname = t.get("name", "")
        tdir = data_dir / tname
        for c in t.get("costumes", []):
            md5 = c.get("md5ext", "")
            if md5 and not (tdir / md5).exists():
                errors.append((f"data/{tname}/{md5}",
                               f"Missing asset for costume {c.get('name')!r}"))
        for s in t.get("sounds", []):
            md5 = s.get("md5ext", "")
            if md5 and not (tdir / md5).exists():
                errors.append((f"data/{tname}/{md5}",
                               f"Missing asset for sound {s.get('name')!r}"))
    return errors


def _validate_runtime(output_dir, ir_data):
    """Headless smoke test: import all modules, register with Engine, run 1 frame."""
    errors = []
    import sys
    import importlib
    import os
    old_cwd = os.getcwd()
    os.chdir(output_dir)
    sys.path.insert(0, ".")
    try:
        from _engine import Engine
        eng = Engine()
        # register all targets
        for idx, t in enumerate(ir_data.get("targets", [])):
            mod_name = f"{idx:02d}_{pyname(t['name'])}"
            try:
                mod = importlib.import_module(mod_name)
                mod.register(eng)
            except Exception as e:
                errors.append((mod_name, f"Register failed: {e}"))
        # run one frame (logic only, no display)
        try:
            eng.set_input({}, {})
            eng.run_frame()
        except Exception as e:
            errors.append(("runtime", f"run_frame failed: {e}"))
    except Exception as e:
        errors.append(("runtime", f"Import/setup failed: {e}"))
    finally:
        os.chdir(old_cwd)
        # clean up sys.modules entries we added
        for k in list(sys.modules.keys()):
            if k.startswith(("00_", "01_", "02_", "03_", "04_", "05_", "06_",
                             "07_", "08_", "09_", "10_", "11_", "12_", "13_",
                             "14_", "15_", "16_", "17_", "18_", "19_", "20_")):
                sys.modules.pop(k, None)
    return errors


# ===================================================================
#  CLI
# ===================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Decompile a Scratch SB3 project.json into Python modules.",
    )
    parser.add_argument("project", help="Path to project.json or .sb3 file")
    parser.add_argument("subcommand", nargs="?", default="all",
                        choices=["extract", "parse", "emit", "all"],
                        help="Pipeline step (default: all)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output path (dir for extract/emit/all, file for parse)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--target-fps", type=float, default=60.0,
                        help="Target frames per second (default: 60)")
    parser.add_argument("--scale", type=int, default=2,
                        help="Display scale factor (default: 2)")
    parser.add_argument("--stage-w", type=int, default=480,
                        help="Stage width in Scratch units (default: 480)")
    parser.add_argument("--stage-h", type=int, default=360,
                        help="Stage height in Scratch units (default: 360)")
    parser.add_argument("--debug", action="store_true",
                        help="Emit debug logging into generated modules")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite the output directory if it already exists")

    args = parser.parse_args()
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    opts = {
        "target_fps": args.target_fps,
        "scale": args.scale,
        "stage_w": args.stage_w,
        "stage_h": args.stage_h,
        "debug": args.debug,
    }

    project = Path(args.project)
    # default output dir named after the project file (stem)
    default_out = Path(project.stem)

    if args.subcommand in ("extract",):
        out = Path(args.output) if args.output else (default_out / "extracted")
        extract_text(project, out)
        print(f"Text extracted to {out}/")
        return

    if args.subcommand in ("parse",):
        ir_data = parse_project(project)
        out = Path(args.output) if args.output else (default_out / "ir.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(ir_data, f, indent=1)
        print(f"IR written to {out}")
        return

    if args.subcommand in ("all",):
        log.info("Running full pipeline: parse + emit")
        out = Path(args.output) if args.output else default_out
        if out.exists() and not args.force:
            sys.exit(f"Output directory {out} already exists. Use --force to overwrite.")
        if args.force and out.exists():
            import shutil
            def _on_rm_error(func, path, exc):
                try:
                    import stat
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except Exception:
                    pass
            shutil.rmtree(out, onerror=_on_rm_error)
        out.mkdir(parents=True, exist_ok=True)
        if project.suffix.lower() == ".sb3":
            project_data = extract_sb3(project, out)
            ir_data = parse_project(project_data)
        else:
            ir_data = parse_project(project)
        emit_python(ir_data, out, opts)
        print(f"Decompiled Python modules written to {out}/")
        return

    if args.subcommand in ("emit",):
        if args.output and Path(args.output).is_dir():
            ir_path = Path("ir.json")
        else:
            ir_path = Path(args.output) if args.output else Path("ir.json")

        if not ir_path.exists():
            print(f"IR file not found: {ir_path}. Run 'parse' first or use 'all'.",
                  file=sys.stderr)
            sys.exit(1)

        with open(ir_path, encoding="utf-8") as f:
            ir_data = json.load(f)
        out_dir = Path(args.output) if args.output and Path(args.output).is_dir() else default_out
        emit_python(ir_data, out_dir, opts)
        print(f"Decompiled Python modules written to {out_dir}/")
        return


if __name__ == "__main__":
    main()