import shlex


def parse_kv_pairs(s: str) -> dict:
    lexer = shlex.shlex(s.replace(" ", ""), posix=True)
    lexer.whitespace = ","
    lexer.wordchars += "=-"
    # noinspection PyTypeChecker
    return dict(word.split("=", maxsplit=1) for word in lexer)
