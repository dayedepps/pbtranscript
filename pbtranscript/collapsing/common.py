#!/usr/bin/env python

"""
Class CollapsedFiles defines files produced by
(1) collapsing isoforms, with/without merging fuzzy junctions
(2) making abundnace file and read status file
(3) filtering by FL count
(4) FIltering by subset
"""

__all__ = ["CollapsedFiles", "FilteredFiles"]


class CollapsedFiles(object):
    """
    Class defines output files produced by collapsing isoforms, making abundance
    and filtering by count | subset.
    """
    def __init__(self, prefix, allow_extra_5exon):
        self.prefix = prefix
        self.allow_extra_5exon = allow_extra_5exon

    @property
    def has_5merge_str(self):
        """Return whether or not merge shorter 5 exons."""
        return "5merge" if self.allow_extra_5exon else "no5merge"

    @property
    def _collapsed_prefix(self):
        return "%s.%s.collapsed" % (self.prefix, self.has_5merge_str)

    def rep_fn(self, suffix):
        """Return FASTA/FASTQ/ContigSet file of collapsed (unfiltered) isoforms"""
        assert suffix in ("fasta", "fastq", "contigset.xml")
        return "%s.rep.%s" % (self._collapsed_prefix, suffix)

    @property
    def gff_fn(self):
        """Return a GFF file, final GFF output, pointing to good_gff_fn."""
        return "%s.gff" % self._collapsed_prefix

    @property
    def good_gff_fn(self):
        """Return a GFF file, which saves good collapsed (unfiltered)
        isoforms, regardless of whether or not fuzzy junctions are further collapsed.
        """
        return "%s.good.gff" % self._collapsed_prefix

    @property
    def bad_gff_fn(self):
        """Return a GFF file, which saves bad collapsed (unfiltered)
        isoforms, regardless of whether or not fuzzy junctions are further collapsed.
        """
        return "%s.bad.gff" % self._collapsed_prefix

    @property
    def group_fn(self):
        """Return a group.txt file, which associated collapsed (unfiltered)
        isoforms with uncollapsed isoforms."""
        return "%s.group.txt" % self._collapsed_prefix

    @property
    def read_stat_fn(self):
        """Return a read_stat.txt file containigng read status of FL and nFL reads."""
        return "%s.read_stat.txt" % self._collapsed_prefix

    @property
    def ignored_ids_txt_fn(self):
        """Return a ignored_ids.txt file which saves ignored uncollapsed isoforms
        which do not meet min_aln_coverage, or min_aln_identity criteria."""
        return "%s.ignored_ids.txt" % self._collapsed_prefix

    @property
    def abundance_fn(self):
        """Return an abundance.txt file of collapsed (unfiltered) isoforms."""
        return "%s.abundance.txt" % self._collapsed_prefix

    @property
    def good_fuzzy_gff_fn(self):
        """Return a file to save good collapsed (unfiltered) isoforms,
        of which fuzzy junctions are further collapsed."""
        return self.good_gff_fn + ".fuzzy"

    @property
    def good_unfuzzy_gff_fn(self):
        """Return a file to save good collapsed (unfiltered) isoforms,
        of which fuzzy junctions are NOT further collapsed."""
        return self.good_gff_fn + ".unfuzzy"

    @property
    def bad_fuzzy_gff_fn(self):
        """Return a file to save bad collapsed (unfiltered) isoforms,
        of which fuzzy junctions are further collapsed."""
        return self.bad_gff_fn + ".fuzzy"

    @property
    def bad_unfuzzy_gff_fn(self):
        """Return a file to save bad collapsed (unfiltered) isoforms,
        of which fuzzy junctions are NOT further collapsed."""
        return self.bad_gff_fn + ".unfuzzy"

    @property
    def fuzzy_group_fn(self):
        """Return a group.txt file which associates collapsed (unfiltered)
        isoforms with uncollapsed isoforms, of which fuzzy junctions are
        further collapsed."""
        return self.group_fn + ".fuzzy"

    @property
    def unfuzzy_group_fn(self):
        """Return a group.txt file which associates collapsed (unfiltered)
        isoforms with uncollapsed isoforms, of which fuzzy junctions are
        NOT further collapsed."""
        return self.group_fn + ".unfuzzy"


class FilteredFiles(CollapsedFiles):
    """
    Class defines collapsed, filtered output files using filter_by_count
    and filter_out_subsets.
    """
    def __init__(self, prefix, allow_extra_5exon, min_count, filter_out_subsets):
        super(FilteredFiles, self).__init__(prefix, allow_extra_5exon=allow_extra_5exon)
        self.min_count = int(min_count)
        self.filter_out_subsets = bool(filter_out_subsets)

    @property
    def _filtered_prefix(self):
        """Return prefix for collapsed, filtered isoforms."""
        _prefix = ".".join([self._collapsed_prefix, "min_fl_%d" % self.min_count])
        return _prefix + ".no_subsets" if self.filter_out_subsets else _prefix

    def filtered_rep_fn(self, suffix):
        """Return a FASTA/FASTQ/ContigSet file of collapsed, filtered isoforms"""
        return "%s.rep.%s" % (self._filtered_prefix, suffix)

    @property
    def filtered_gff_fn(self):
        """Return a GFF file of collapsed, filtered isoforms."""
        return "%s.gff" % self._filtered_prefix

    @property
    def filtered_abundance_fn(self):
        """Return an abundance.txt file of collapsed, filtered isoforms."""
        return "%s.abundance.txt" % self._filtered_prefix
