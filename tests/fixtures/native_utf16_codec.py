"""Ordinary Python oracle program for explicit-endian UTF-16 codecs."""


def encoded(text, codec, errors):
    try:
        print('encoded', list(text.encode(codec, errors)))
    except UnicodeEncodeError as error:
        print('encode-error', error.encoding, error.start, error.end,
              error.reason, error.object == text)
    except LookupError as error:
        print('lookup-error', type(error).__name__)


def decoded(data, codec, errors):
    try:
        print('decoded', [ord(char) for char in data.decode(codec, errors)])
    except UnicodeDecodeError as error:
        print('decode-error', error.encoding, error.start, error.end,
              error.reason, error.object == data)
    except LookupError as error:
        print('lookup-error', type(error).__name__)


def main():
    texts = ['', 'A\0z', 'café', chr(0xffff), chr(0x10000), chr(0x10ffff),
             chr(0xd800), chr(0xdc00), chr(0xdc80), chr(0xdc80) + chr(0xdc81),
             chr(0xd800) + chr(0xdc00), 'A' + chr(0xd800) + chr(0xd801) + 'B']
    policies = ['strict', 'ignore', 'replace', 'surrogatepass', 'surrogateescape',
                'backslashreplace', 'xmlcharrefreplace', 'namereplace', 'missing-handler']
    for codec in ['utf-16-le', 'utf-16-be']:
        for text in texts:
            for errors in policies:
                encoded(text, codec, errors)
        for text in ['', 'A\0z', 'café', chr(0xffff), chr(0x10000), chr(0x10ffff)]:
            data = text.encode(codec)
            decoded(data, codec, 'strict')
            print('bytearray', bytearray(data).decode(codec) == text)
            print('constructor', str(data, codec) == text)
        for data in [b'', b'A', b'\x80', b'\x00\xd8', b'\xd8\x00',
                     b'\x00\xdc', b'\xdc\x00', b'\x00\xd8A', b'\xd8\x00A',
                     b'\x00\xd8A\x00', b'\xd8\x00\x00A',
                     b'\xff\xd8A\x00', b'\xd8\xff\x00A',
                     b'\xff\xfe', b'\xfe\xff', b'\x3d\xd8\x00\xde']:
            for errors in ['strict', 'ignore', 'replace', 'surrogatepass',
                           'surrogateescape', 'backslashreplace', 'missing-handler']:
                decoded(data, codec, errors)
    for alias in ['UTF 16 LE', 'utf_16le', 'csutf16le', 'unicodelittleunmarked',
                  'UTF 16 BE', 'utf_16be', 'csutf16be', 'unicodebigunmarked']:
        print('alias', 'café'.encode(alias).decode(alias) == 'café')
    # Packed integer policies for existing codecs must remain unchanged.
    print('old-codecs', chr(233).encode('ascii', 'replace'),
          chr(233).encode('latin1'), b'\xff'.decode('utf8', 'ignore'))


main()
