void attrib_report(const GU_Detail *gdp) {
    for (
        GA_AttributeDict::iterator it = gdp->getAttributeDict(GA_ATTRIB_POINT).begin(GA_SCOPE_PUBLIC);
        !it.atEnd();
        ++it
    )
    {
        const GA_Attribute *attrib = it.attrib();
        std::cout << attrib->getFullName() << std::endl;    
    }
}
