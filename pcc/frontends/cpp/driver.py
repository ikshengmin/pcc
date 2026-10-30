"""Owned C++ translation-unit selection and preprocessing.

Reuse the owned token preprocessor, never the C extension cleanup layer or an
external C++ tool. Unsupported language and library features remain diagnostics.
"""


def is_cpp_path(path):
    return path.endswith((".cc", ".cpp", ".cxx", ".c++", ".C"))


def preprocess_cpp(source, base_dir=None, include_dirs=None, cpp_args=None,
                   target_triple=None):
    from pcc.frontends.c.preprocessor import preprocess

    # The C compatibility headers are not C++ standard-library implementations.
    # Explicit include paths remain available and missing headers fail closed.
    arguments = ["-nostdinc", "-U__STDC__", "-U__STDC_VERSION__", "-D__cplusplus=201703L"]
    values = list(cpp_args or ())
    index = 0
    while index < len(values):
        argument = values[index]
        index += 1
        if argument in ("-D", "-U", "-I", "-isystem", "-include"):
            arguments.append(argument)
            if index < len(values):
                arguments.append(values[index])
                index += 1
            continue
        if argument == "-std=c++17":
            continue
        if argument.startswith("-std=") or argument == "-ansi":
            raise ValueError("owned C++ frontend requires -std=c++17: " + argument)
        arguments.append(argument)
    return preprocess(source, base_dir=base_dir, include_dirs=include_dirs,
                      cpp_args=arguments, target_triple=target_triple, language="c++17")
