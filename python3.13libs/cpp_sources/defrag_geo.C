bool defragment(GU_Detail *gdp, bool fill_holes) {
    UT_Options defrag_opts;
    defrag_opts.setOptionB("removeholes", fill_holes);
    return gdp->defragment(&defrag_opts);
}
