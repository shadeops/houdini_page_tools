void compress_pages(GU_Detail *gdp) {
    for (
        GA_AttributeDict::iterator it = gdp->getAttributeDict(GA_ATTRIB_POINT).begin(GA_SCOPE_PUBLIC);
        !it.atEnd();
        ++it
    )
    {
        GA_Attribute *attrib = it.attrib();
        attrib->tryCompressAllPages();
        //std::cout << attrib->getFullName() << std::endl;    
    }
}
