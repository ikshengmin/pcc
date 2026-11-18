"""Rust character tokens: one Unicode scalar, distinct from a lifetime and u8."""
_HEX = frozenset("0123456789abcdefABCDEF")
_ESCAPES = {"n":"\n","r":"\r","t":"\t","0":"\0","\\":"\\","'":"'","\"":"\""}


def scan_quote(source,start,token):
    i=start+1
    if i>=len(source):
        raise token.error("unterminated Rust character literal or lifetime")
    c=source[i]
    if c=="\\":
        i+=1
        code=source[i:i+1]
        if code in _ESCAPES:
            value=_ESCAPES[code];i+=1
        elif code=="x":
            digits=source[i+1:i+3]
            if len(digits)!=2 or digits[0] not in "01234567" or digits[1] not in _HEX:
                raise token.error("invalid 7-bit ASCII character escape")
            value=chr(int(digits,16));i+=3
        elif code=="u" and source[i+1:i+2]=="{":
            close=source.find("}",i+2)
            digits=source[i+2:close] if close>=0 else ""
            clean=digits.replace("_","")
            if (not digits or digits[0]=="_" or not 1<=len(clean)<=6 or
                    any(d not in _HEX for d in clean)):
                raise token.error("invalid Unicode character escape")
            scalar=int(clean,16)
            if scalar>0x10ffff or 0xd800<=scalar<=0xdfff:
                raise token.error("character escape is not a Unicode scalar")
            value=chr(scalar);i=close+1
        else:
            raise token.error("unsupported or invalid Rust character escape")
    elif source[i+1:i+2]=="'":
        if c in "'\r\n\t" or 0xd800<=ord(c)<=0xdfff:
            raise token.error("invalid unescaped Rust character literal")
        value=c;i+=1
    elif c.isascii() and (c.isalpha() or c=="_"):
        end=i+1
        while end<len(source) and source[end].isascii() and (source[end].isalnum() or source[end]=="_"):
            end+=1
        if source[end:end+1]=="'":
            raise token.error("a character literal contains exactly one Unicode scalar")
        return "lifetime",None,end
    else:
        raise token.error("invalid or unterminated Rust character literal")
    if source[i:i+1]!="'":
        raise token.error("a character literal contains exactly one Unicode scalar")
    end=i+1
    if end<len(source) and source[end].isidentifier():
        raise token.error("character literal suffixes are not implemented")
    return "character",value,end
