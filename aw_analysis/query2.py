import logging
from typing import Union, List, Callable, Any
from datetime import datetime

from aw_core.models import Event
from aw_datastore import Datastore

from .query2_functions import query2_functions

logger = logging.getLogger(__name__)


class QueryException(Exception):
    pass


class Token:
    def interpret(self, datastore: Datastore, namespace: dict):
        raise NotImplementedError

    @staticmethod
    def parse(string: str, namespace: dict):
        raise NotImplementedError

    @staticmethod
    def check(string: str):
        raise NotImplementedError


class Integer(Token):
    def __init__(self, value) -> None:
        self.value = value

    def interpret(self, datastore: Datastore, namespace: dict):
        return self.value

    @staticmethod
    def parse(string: str, namespace: dict={}) -> Token:
        return Integer(int(string))

    @staticmethod
    def check(string: str):
        token = ""
        for char in string:
            if char.isdigit():
                token += char
            else:
                break
        return token, string[len(token):]


class Variable(Token):
    def __init__(self, name, value) -> None:
        self.name = name
        self.value = value

    def interpret(self, datastore: Datastore, namespace: dict):
        namespace[self.name] = self.value
        return self.value

    @staticmethod
    def parse(string: str, namespace: dict) -> Token:
        val = None
        if string in namespace:
            val = namespace[string]
        return Variable(string, val)

    @staticmethod
    def check(string: str):
        token = ""
        for i, char in enumerate(string):
            if char.isalpha() or char == '_':
                token += char
            elif i != 0 and char.isdigit():
                token += char
            else:
                break
        return token, string[len(token):]


class String(Token):
    def __init__(self, value):
        self.value = value

    def interpret(self, datastore: Datastore, namespace: dict):
        return self.value

    @staticmethod
    def parse(string: str, namespace: dict={}) -> Token:
        string = string[1:-1]
        return String(string)

    @staticmethod
    def check(string: str):
        token = ""
        quotes_type = string[0]
        if quotes_type != '"' and quotes_type != "'":
            return token, string
        token += quotes_type
        for char in string[1:]:
            token += char
            if char == quotes_type:
                break
        if token[-1] != quotes_type or len(token) < 2:
            # Unclosed string?
            raise QueryException("Failed to parse string")
        return token, string[len(token):]


class Function(Token):
    def __init__(self, name, args):
        self.name = name
        self.args = args

    def interpret(self, datastore: Datastore, namespace: dict):
        if self.name not in query2_functions:
            raise QueryException("Tried to call function '{}' which doesn't exist".format(self.name))
        call_args = [datastore, namespace]
        for arg in self.args:
            call_args.append(arg.interpret(datastore, namespace))
        logger.debug("Arguments for functioncall to {} is {}".format(self.name, call_args))
        try:
            result = query2_functions[self.name](*call_args)  # type: ignore
        except TypeError:
            raise QueryException("Tried to call function {} with invalid amount of arguments".format(self.name))
        return result

    @staticmethod
    def parse(string: str, namespace: dict) -> Token:
        arg_start = 0
        arg_end = len(string) - 1
        # Find opening bracket
        for char in string:
            if char == '(':
                break
            arg_start = arg_start + 1
        # Parse name
        name = string[:arg_start]
        # Parse arguments
        args = []
        args_str = string[arg_start + 1:arg_end]
        while args_str:
            (arg_t, arg), args_str = _parse_token(args_str, namespace)
            comma = args_str.find(",")
            if comma != -1:
                args_str = args_str[comma + 1:]
            args.append(arg_t.parse(arg, namespace))
        return Function(name, args)

    @staticmethod
    def check(string: str):
        i = 0
        # Find opening bracket
        found = False
        for char in string:
            if char.isalpha() or char == "_":
                i = i + 1
            elif i != 0 and char.isdigit():
                i = i + 1
            elif char == '(':
                i = i + 1
                found = True
                break
            else:
                break
        if not found:
            return None, string
        found = False
        single_quote = False
        double_quote = False
        for char in string:
            i = i + 1
            if char == "'":
                single_quote = not single_quote
            elif char == '"':
                double_quote = not double_quote
            elif double_quote or single_quote:
                pass
            elif i != 0 and char.isdigit():
                pass
            elif char == ')':
                break
        return string[:i], string[i + 1:]


class Dict(Token):
    def __init__(self, value: dict) -> None:
        self.value = value

    def interpret(self, datastore: Datastore, namespace: dict):
        expanded_dict = {}
        for key, value in self.value.items():
            expanded_dict[key] = value.interpret(datastore, namespace)
        return expanded_dict

    @staticmethod
    def parse(string: str, namespace: dict) -> Token:
        entries_str = string[1:-1]
        d = {}
        while len(entries_str) > 0:
            entries_str = entries_str.strip()
            if len(d) > 0 and entries_str[0] == ",":
                entries_str = entries_str[1:]
            # parse key
            (key_t, key_str), entries_str = _parse_token(entries_str, namespace)
            if key_t != String:
                raise QueryException("Key in dict is not a str")
            key = String.parse(key_str).value  # type: ignore
            entries_str = entries_str.strip()
            # Remove :
            if entries_str[0] != ":":
                raise QueryException("Key in dict is not followed by a :")
            entries_str = entries_str[1:]
            # parse val
            (val_t, val_str), entries_str = _parse_token(entries_str, namespace)
            if not val_t:
                raise QueryException("Dict expected a value, got nothing")
            val = val_t.parse(val_str, namespace)
            # set
            d[key] = val
        return Dict(d)

    @staticmethod
    def check(string: str):
        if string[0] != '{':
            return None, string
        # Find closing bracket
        i = 1
        to_consume = 1
        single_quote = False
        double_quote = False
        for char in string[i:]:
            i += 1
            if char == "'":
                single_quote = not single_quote
            elif char == '"':
                double_quote = not double_quote
            elif double_quote or single_quote:
                pass
            elif char == '}':
                to_consume = to_consume - 1
            elif char == '{':
                to_consume = to_consume + 1
            if to_consume == 0:
                break
        return string[:i], string[i + 1:]


class List(Token):
    def __init__(self, value: dict) -> None:
        self.value = value

    def interpret(self, datastore: Datastore, namespace: dict):
        expanded_list = []
        for value in self.value:
            expanded_list.append(value.interpret(datastore, namespace))
        return expanded_list

    @staticmethod
    def parse(string: str, namespace: dict) -> Token:
        entries_str = string[1:-1]
        l = []
        while len(entries_str) > 0:
            entries_str = entries_str.strip()
            if len(l) > 0 and entries_str[0] == ",":
                entries_str = entries_str[1:]
            # parse
            (val_t, val_str), entries_str = _parse_token(entries_str, namespace)
            if not val_t:
                raise QueryException("List expected a value, got nothing")
            val = val_t.parse(val_str, namespace)
            # set
            l.append(val)
        return List(l)

    @staticmethod
    def check(string: str):
        if string[0] != '[':
            return None, string
        # Find closing bracket
        i = 1
        to_consume = 1
        single_quote = False
        double_quote = False
        for char in string[i:]:
            i += 1
            if char == "'":
                single_quote = not single_quote
            elif char == '"':
                double_quote = not double_quote
            elif double_quote or single_quote:
                pass
            elif char == ']':
                to_consume = to_consume - 1
            elif char == '[':
                to_consume = to_consume + 1
            if to_consume == 0:
                break
        return string[:i], string[i + 1:]


def _parse_token(string: str, namespace: dict): # TODO: Add return type
    # TODO: The whole parsing thing is shoddily written, needs a rewrite from ground-up
    if not isinstance(string, str):
        raise QueryException("Reached unreachable, cannot parse something that isn't a string")
    if len(string) == 0:
        return (None, ""), string
    string = string.strip()
    types = [String, Integer, Function, Dict, List, Variable]  # type: List[Any]
    token = None
    t = None  # Declare so we can return it
    for t in types:
        token, string = t.check(string)
        if token:
            break
    if not token:
        raise QueryException("Syntax error: {}".format(string))
    return (t, token), string


def create_namespace() -> dict:
    namespace = {
        "TRUE": 1,
        "FALSE": 0,
    }
    return namespace


def parse(line, namespace):
    separator_i = line.find("=")
    var_str = line[:separator_i]
    val_str = line[separator_i + 1:]
    if not val_str:
        # TODO: Proper message
        raise QueryException("Nothing to assign")
    (var_t, var), var_str = _parse_token(var_str, namespace)
    var_str = var_str.strip()
    if var_str:  # Didn't consume whole var string
        raise QueryException("Invalid syntax for assignment variable")
    if var_t is not Variable:
        raise QueryException("Cannot assign to a non-variable")
    (val_t, val), var_str = _parse_token(val_str, namespace)
    if var_str:  # Didn't consume whole val string
        raise QueryException("Invalid syntax for value to assign")
    # Parse token
    var = var_t.parse(var, namespace)
    val = val_t.parse(val, namespace)
    return var, val


def interpret(var, val, namespace, datastore):
    namespace[var.name] = val.interpret(datastore, namespace)
    logger.debug("Set {} to {}".format(var.name, namespace[var.name]))


def get_return(namespace):
    if "RETURN" not in namespace:
        raise QueryException("Query doesn't assign the RETURN variable, nothing to respond")
    return namespace["RETURN"]


def query(name: str, query: str, starttime: datetime, endtime: datetime, datastore: Datastore) -> None:
    namespace = create_namespace()
    namespace["NAME"] = name
    namespace["STARTTIME"] = starttime.isoformat()
    namespace["ENDTIME"] = endtime.isoformat()

    query_stmts = query.split(";")
    for statement in query_stmts:
        statement = statement.strip()
        if statement:
            logger.debug("Parsing: " + statement)
            var, val = parse(statement, namespace)
            interpret(var, val, namespace, datastore)

    result = get_return(namespace)
    return result
