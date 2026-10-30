// PCC-authored integration driver, not an upstream TinyXML2 test-suite copy.
#include "tinyxml2.h"
#include <cstdio>
#include <cstring>

int main() {
    tinyxml2::XMLDocument document;
    if (document.Parse("<root value=\"42\">hello&amp;world</root>") != tinyxml2::XML_SUCCESS)
        return 1;
    const tinyxml2::XMLElement* root = document.RootElement();
    if (!root || std::strcmp(root->Name(), "root") != 0)
        return 2;
    int value = -1;
    if (root->QueryIntAttribute("value", &value) != tinyxml2::XML_SUCCESS || value != 42)
        return 3;
    if (!root->GetText() || std::strcmp(root->GetText(), "hello&world") != 0)
        return 4;
    tinyxml2::XMLDocument malformed;
    if (malformed.Parse("<root>") == tinyxml2::XML_SUCCESS)
        return 5;
    tinyxml2::XMLPrinter printer;
    document.Print(&printer);
    tinyxml2::XMLDocument roundtrip;
    if (roundtrip.Parse(printer.CStr()) != tinyxml2::XML_SUCCESS)
        return 6;
    if (!roundtrip.RootElement() || roundtrip.RootElement()->IntAttribute("value") != 42)
        return 7;
    std::printf("tinyxml2:%d:%s\n", value, root->GetText());
    return 0;
}
