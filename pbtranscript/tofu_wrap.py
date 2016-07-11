#!/usr/bin/env python

"""
Script tofu_wrap.py is designed to analyze FL/nFL CCS reads of
PacBio cDNA samples. It separates CCS reads into bins, applies
Iterative Clustering and Error Correction (ICE) algorithm to
get isoform clusters, calls Arrow (Quiver) to polish isoform
clusters. If a GMAP reference genome database is provided,
the tofu_wrap.py maps polished isoform clusters to reference
genome, collapses redundant isoform clusters into groups and
produce annotations of collapsed isoform groups in GFF, counts
abundance of supportive FL and nFL CCS reads of collapsed groups,
and filters collapsed groups based on abundance info.

Procedure:
    (1) separate flnc into bins
    (2) apply 'pbtranscript cluster' to each bin
    (3) merge polished isoform cluster from all bins
    (4) collapse polished isoform clusters into groups
    (5) count abundance info of collapsed groups
    (6) filter collapsed groups based on abundance info
"""

import os.path as op
import subprocess

import sys
import argparse
import logging
import time

from pbcommand.utils import setup_log
from pbcommand.cli.core import pacbio_args_runner

from pbtranscript.__init__ import get_version
from pbtranscript.Utils import ln, mkdir, realpath, get_sample_name, \
    rmpath, guess_file_format, FILE_FORMATS
from pbtranscript.ClusterOptions import IceOptions, SgeOptions, IceQuiverHQLQOptions
from pbtranscript.PBTranscriptOptions import add_nfl_fa_argument, \
    add_fofn_arguments, add_ice_arguments, add_sge_arguments, \
    add_ice_post_quiver_hq_lq_qv_arguments

from pbtranscript.Cluster import Cluster
from pbtranscript.CombineUtils import CombinedFiles, CombineRunner
from pbtranscript.separate_flnc import SeparateFLNCRunner, SeparateFLNCBase

from pbtranscript.ice.IceUtils import check_blasr
from pbtranscript.ice.IceQuiverPostprocess import IceQuiverPostprocess
from pbtranscript.collapsing.CollapsingUtils import map_isoforms_and_sort
from pbtranscript.tasks.map_isoforms_to_genome import gmap_db_and_name_from_ds
from pbtranscript.tasks.post_mapping_to_genome import add_post_mapping_to_genome_arguments, \
        post_mapping_to_genome_runner
from pbtranscript.tasks.separate_flnc import add_separate_flnc_arguments
from pbtranscript.tasks.map_isoforms_to_genome import add_gmap_arguments


__author__ = "etseng@pacb.com"

log = logging.getLogger(__name__)


def _sanity_check_args(args):
    """Sanity check tofu arguments."""
    # Check required arguments
    if args.nfl_fa is None:
        raise ValueError("--nfl_fa must be provided for tofu_wrap. Quit.")
    if args.bas_fofn is None:
        raise ValueError("--bas_fofn must be provided for polishing isoforms. Quit.")
    if not args.quiver: # overwrite --quiver
        logging.warning("Overwrite --quiver to True for tofu_wrap. Continue.")
        args.quiver = True

    # check gmap reference genome
    if all(arg is None for arg in [args.gmap_db, args.gmap_name, args.gmap_ds]):
        raise ValueError("GMAP reference Database is not set! Quit.")
    # overwrite args.gmap_db, args.gmap_name if args.gmap_ds is not None
    if args.gmap_ds is not None:
        args.gmap_db, args.gmap_name = gmap_db_and_name_from_ds(args.gmap_ds)
    # check gmap dir existence
    if not op.exists(args.gmap_db):
        raise IOError("GMAP DB location not valid: %s. Quit.", args.gmap_db)
    if not op.exists(op.join(args.gmap_db, args.gmap_name)):
        raise IOError("GMAP name not valid: %s. Quit.", args.gmap_name)

    # check input format: bax.h5/bas.h5 must.
    if guess_file_format(args.bas_fofn) != FILE_FORMATS.BAM:
        raise ValueError("--bas_fofn %s must be either BAM or subreadset.xml." % args.bas_fofn +
                         "Bax.h5 must be converted to BAM using bax2bam first! " +
                         "Multiple BAM files can be merged to a BAM FOFN or dataset xml.")

    # check output file format
    if not any(args.collapsed_filtered_fn.endswith(ext) for ext in
               (".fa", ".fasta", ".fq", ".fastq")):
        raise ValueError("Output file %s must be FASTA or FASTQ!" % args.collapsed_filtered_fn)

    # check blasr version
    check_blasr()


def add_tofu_output_arguments(parser):
    """Add tofu output arguments"""
    out_group = parser.add_argument_group("Tofu output arguments")

    helpstr = "Directory to store tofu output files (default: tofu_out)"
    out_group.add_argument("-d", "--tofu_dir", "--outDir", type=str, dest="tofu_dir",
                           default="tofu_out", help=helpstr)
    helpstr = "Sample name. If not given, a random ID is generated"
    out_group.add_argument("--sample_name", "--output_seqid_prefix", dest="sample_name",
                           type=str, default=None, help=helpstr)

    helpstr = "Output GFF file containing collapsed filtered isoform groups " + \
              "(default: <output_prefix>.gff)"
    out_group.add_argument("--gff", default=None, type=str, dest="gff_fn", help=helpstr)
    helpstr = "Output group TXT file which associates collapsed filtered isoform groups " + \
              "with member isoforms (default: <output_prefix>.group.txt)."
    out_group.add_argument("--group", default=None, type=str, dest="group_fn", help=helpstr)
    helpstr = "Output abundance TXT file counting supportive FL/nFL CCS reads of collapsed " + \
              "filtered isoform groups (default: <output_prefix>.abundance.txt)"
    out_group.add_argument("--abundance", default=None, type=str, dest="abundance_fn", help=helpstr)
    helpstr = "Output read stat TXT file which associates CCS reads with collapsed isoform " + \
              "groups (default: <output_prefix>.read_stat.txt)"
    out_group.add_argument("--read_stat", default=None, type=str, dest="read_stat_fn", help=helpstr)
    helpstr = "Output cluster summary JSON file (default: <tofu_dir>/combined/all.cluster_summary.json)"
    out_group.add_argument("--summary", default=None, type=str, dest="summary_fn", help=helpstr)
    helpstr = "Output cluster report CSV file (default: <tofu_dir>/combined/all.cluster_report.csv)"
    out_group.add_argument("--report", default=None, type=str, dest="report_fn", help=helpstr)

    return parser


def get_parser():
    """Returns arg parser."""
    parser = argparse.ArgumentParser(prog='tofu_wrap')

    helpstr = "Input full-length non-chimeric reads in FASTA or ContigSet format " + \
              "(e.g., isoseq_flnc.fasta|contigset.xml)"
    parser.add_argument("flnc_fa", type=str, help=helpstr)
    helpstr = "Output collapsed filtered isoforms in FASTA/FASTQ format (e.g., tofu_out.fastq)"
    parser.add_argument("collapsed_filtered_fn", type=str, help=helpstr)

    parser = add_nfl_fa_argument(parser, positional=False, required=True)
    parser.add_argument("--nfl_reads_per_split", type=int,
                        dest="nfl_reads_per_split", default=60000,
                        help="Number of nFL reads per split file (default: 60000)")
    parser = add_fofn_arguments(parser, ccs_fofn=True, bas_fofn=True, fasta_fofn=True)

    # tofu output arguments
    parser = add_tofu_output_arguments(parser)

    parser = add_ice_arguments(parser) # Add Ice options, including --quiver
    parser = add_sge_arguments(parser, blasr_nproc=True, quiver_nproc=True, gcon_nproc=True) # Sge
    parser = add_ice_post_quiver_hq_lq_qv_arguments(parser) # IceQuiver HQ/LQ QV options.

    parser = add_separate_flnc_arguments(parser) # separate_flnc options
    parser = add_gmap_arguments(parser) # map to gmap reference options
    parser = add_post_mapping_to_genome_arguments(parser) # post mapping to genome options

    misc_group = parser.add_argument_group("Misc arguments")
    misc_group.add_argument("--mem_debug", default=False, action="store_true",
                            help=argparse.SUPPRESS)
    misc_group.add_argument("--keep_tmp_files", default=False, action="store_true",
                            help="False: delete tmp files; True: keep tmp files (default: False).")
    misc_group.add_argument("--version", action='version', version='%(prog)s ' + str(get_version()))
    return parser


class TofuFiles(CombinedFiles):
    """All input/output files used by tofu_wrap."""
    def __init__(self, tofu_dir):
        self.tofu_dir = tofu_dir
        super(TofuFiles, self).__init__(combined_dir=op.join(tofu_dir, "combined"))

    @property
    def fasta_fofn_files_dir(self):
        """Return directory for storing fasta files converted from subreads.bax/bas/bam files."""
        return op.join(self.tofu_dir, "fasta_fofn_files")

    @property
    def separate_flnc_pickle(self):
        """A pickle file (e.g., separate_flnc.pickle) containing file paths to binned
        FLNC reads. Usually generated by separate_flnc."""
        return op.join(self.tofu_dir, "separate_flnc.pickle")

    @property
    def sorted_gmap_sam(self):
        """Sorted GMAP sam file which contains alignments mapping HQ isoforms to GMAP reference."""
        return op.join(self.tofu_dir, "sorted_gmap.sam")

    @property
    def tofu_final_fa(self):
        """Return final output collapsed filtered isoforms in FASTA"""
        return op.join(self.tofu_dir, "tofu_final.fasta")

    @property
    def tofu_final_fq(self):
        """Return final output collapsed filtered isoforms in FASTQ"""
        return op.join(self.tofu_dir, "tofu_final.fastq")


def args_runner(args):
    """args runner"""
    logging.info("%s arguments are:\n%s\n", __file__, args)

    # sanity check arguments
    _sanity_check_args(args)

    # make option objects
    ice_opts = IceOptions(quiver=args.quiver, use_finer_qv=args.use_finer_qv,
                          targeted_isoseq=args.targeted_isoseq,
                          ece_penalty=args.ece_penalty, ece_min_len=args.ece_min_len,
                          nfl_reads_per_split=args.nfl_reads_per_split)
    sge_opts = SgeOptions(unique_id=args.unique_id, use_sge=args.use_sge,
                          max_sge_jobs=args.max_sge_jobs, blasr_nproc=args.blasr_nproc,
                          quiver_nproc=args.quiver_nproc, gcon_nproc=args.gcon_nproc,
                          sge_env_name=args.sge_env_name, sge_queue=args.sge_queue)
    ipq_opts = IceQuiverHQLQOptions(qv_trim_5=args.qv_trim_5, qv_trim_3=args.qv_trim_3,
                                    hq_quiver_min_accuracy=args.hq_quiver_min_accuracy)

    # (1) separate flnc reads into bins
    logging.info("Separating FLNC reads into bins.")
    tofu_f = TofuFiles(tofu_dir=args.tofu_dir)
    s = SeparateFLNCRunner(flnc_fa=args.flnc_fa, root_dir=args.tofu_dir,
                           out_pickle=tofu_f.separate_flnc_pickle,
                           bin_size_kb=args.bin_size_kb, bin_by_primer=args.bin_by_primer,
                           bin_manual=args.bin_manual, max_base_limit_MB=args.max_base_limit_MB)
    s.run()

    flnc_files = SeparateFLNCBase.convert_pickle_to_sorted_flnc_files(tofu_f.separate_flnc_pickle)
    logging.info("Separated FLNC reads bins are %s", flnc_files)

    # (2) apply 'pbtranscript cluster' to each bin
    # run ICE/Quiver (the whole thing), providing the fasta_fofn
    logging.info("Running ICE/Polish on separated FLNC reads bins.")
    split_dirs = []
    for flnc_file in flnc_files:
        split_dir = op.join(realpath(op.dirname(flnc_file)), "cluster_out")
        mkdir(split_dir)
        split_dirs.append(split_dir)
        cur_out_cons = op.join(split_dir, "consensus_isoforms.fasta")

        ipq_f = IceQuiverPostprocess(root_dir=split_dir, ipq_opts=ipq_opts)
        if op.exists(ipq_f.quivered_good_fq):
            logging.warning("HQ polished isoforms %s already exist. SKIP!", ipq_f.quivered_good_fq)
            continue
        else:
            logging.info("Running ICE/Quiver on %s", split_dir)
            rmpath(cur_out_cons)

        obj = Cluster(root_dir=split_dir, flnc_fa=flnc_file,
                      nfl_fa=args.nfl_fa,
                      bas_fofn=args.bas_fofn,
                      ccs_fofn=args.ccs_fofn,
                      fasta_fofn=args.fasta_fofn,
                      out_fa=cur_out_cons, sge_opts=sge_opts,
                      ice_opts=ice_opts, ipq_opts=ipq_opts)

        if args.mem_debug: # DEBUG
            from memory_profiler import memory_usage
            start_t = time.time()
            mem_usage = memory_usage(obj.run, interval=60)
            end_t = time.time()
            with open('mem_debug.log', 'a') as f:
                f.write("Running ICE/Quiver on {0} took {1} secs.\n".format(split_dir,
                                                                            end_t-start_t))
                f.write("Maximum memory usage: {0}\n".format(max(mem_usage)))
                f.write("Memory usage: {0}\n".format(mem_usage))
        else:
            obj.run()

        if not args.keep_tmp_files: # by deafult, delete all tempory files.
            logging.info("Deleting %s", ipq_f.tmp_dir)
            subprocess.Popen(['rm', '-rf', '%s' % ipq_f.tmp_dir])
            logging.info("Deleting %s", ipq_f.quivered_dir)
            subprocess.Popen(['rm', '-rf', '%s' % ipq_f.quivered_dir])

    # (3) merge polished isoform cluster from all bins
    logging.info("Merging isoforms from all bins to %s.", tofu_f.combined_dir)
    c = CombineRunner(combined_dir=tofu_f.combined_dir,
                      sample_name=get_sample_name(args.sample_name),
                      split_dirs=split_dirs, ipq_opts=ipq_opts)
    c.run()
    if args.summary_fn is not None:
        ln(tofu_f.all_cluster_summary_fn, args.summary_fn)
    if args.report_fn is not None:
        ln(tofu_f.all_cluster_report_fn, args.report_fn)

    # (4) map HQ isoforms to GMAP reference genome
    map_isoforms_and_sort(input_filename=tofu_f.all_hq_fq, sam_filename=tofu_f.sorted_gmap_sam,
                          gmap_db_dir=args.gmap_db, gmap_db_name=args.gmap_name,
                          gmap_nproc=args.gmap_nproc)

    # (5) post mapping to genome analysis, including
    #     * collapse polished HQ isoform clusters into groups
    #     * count abundance of collapsed isoform groups
    #     * filter collapsed isoforms based on abundance info
    logging.info("Post mapping to genome analysis.")
    out_isoforms = args.collapsed_filtered_fn
    if any(out_isoforms.endswith(ext) for ext in (".fa", ".fasta")):
        in_isoforms = tofu_f.all_hq_fa
    elif any(out_isoforms.endswith(ext) for ext in (".fq", ".fastq")):
        in_isoforms = tofu_f.all_hq_fq
    else:
        raise ValueError("Output file %s must be FASTA or FASTQ!" % out_isoforms)

    post_mapping_to_genome_runner(
        in_isoforms=in_isoforms, in_sam=tofu_f.sorted_gmap_sam,
        in_pickle=tofu_f.hq_lq_prefix_dict_pickle, out_isoforms=args.collapsed_filtered_fn,
        out_gff=args.gff_fn, out_abundance=args.abundance_fn,
        out_group=args.group_fn, out_read_stat=args.read_stat_fn,
        min_aln_coverage=args.min_aln_coverage, min_aln_identity=args.min_aln_identity,
        min_flnc_coverage=args.min_flnc_coverage, max_fuzzy_junction=args.max_fuzzy_junction,
        allow_extra_5exon=args.allow_extra_5exon, min_count=args.min_count)

    return 0


def main(argv=sys.argv[1:]):
    """tofu main."""
    return pacbio_args_runner(
        argv=argv,
        parser=get_parser(),
        args_runner_func=args_runner,
        alog=log,
        setup_log_func=setup_log)


if __name__ == "__main__":
    sys.exit(main())
