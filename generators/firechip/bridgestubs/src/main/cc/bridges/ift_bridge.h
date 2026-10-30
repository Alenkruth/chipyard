// ift_bridge.h — C++ driver for the BOOM IFT FireSim bridge.
//
// Reads 256-bit IFT records from a 512-bit DMA stream and writes them to a
// binary output file.  Each 512-bit beat contains two records (lower 256 bits
// first); a record is valid if its class field (bits[3:0]) is non-zero.
//
// Output file format: raw stream of 32-byte (256-bit) IFT record structs.
// The offline decoders (base-attacks/fuzzer/boom_ift/decode_ift_bridge.py and its
// fast twin) map class, cyc, op_count, influencer slots and the STATE groups from
// the record v3 bit layout defined in boom.v3.exu.IFTRecordV3.

#pragma once

#include "core/bridge_driver.h"
#include "core/clock_info.h"
#include "ift_record_writer.h"   // [V3-DRAIN] SP-25: ring of buffers + one writer thread

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

class ift_bridge_t final : public streaming_bridge_driver_t {
public:
  static char KIND;

  ift_bridge_t(simif_t &sim,
               StreamEngine &stream,
               int wid,
               const std::vector<std::string> &args,
               int stream_idx,
               int stream_depth,
               unsigned int retire_width,
               unsigned int record_bits,
               const ClockInfo &clock_info);
  ~ift_bridge_t();

  void init()   override;
  void tick()   override;
  void finish() override;

  // [CHUNKDRAIN] patch 54 (firesim_top.cc end-of-run drain).  In discard mode tick()
  // still pulls the stream (so the target is never back-pressured) but writes nothing;
  // the beats are counted in discarded_beats, not total_beats.
  void set_discard(bool on) {
    // [DRAINHIST] the run phase ends when the end-of-run drain starts discarding.
    if (on && !discard && dh_run_end_ns == 0) dh_run_end_ns = dh_now_ns();
    discard = on;
  }
  uint64_t beats_seen() const { return total_beats + discarded_beats; }
  uint64_t discarded() const { return discarded_beats; }

private:
  void flush();
  void tick_stream(uint64_t t0);  // [DRAINHIST] the original tick() body, t0 = tick start

  int          stream_idx;
  int          stream_depth;
  unsigned int retire_width;
  unsigned int record_bits;    // always 256
  ClockInfo    clock_info;

  std::string  outfilename;
  FILE        *outfile    = nullptr;
  // [V3-DRAIN] SP-25 (design D10 "okay. do this"): the tick thread copies valid records into a ring of
  // 4 x 4 MB buffers; one writer thread writes each full buffer with one fwrite (outfile is unbuffered).
  ift_record_writer_t writer{4, size_t(4) << 20};
  bool         drain_only = false;

  // [DRVXDMA] Page-aligned, persistent DMA destination buffer (stream_depth beats,
  // rounded up to whole pages).  It MUST be page-aligned: on the XDMA (U250) flow a
  // pull whose destination crosses a host page boundary at an offset that is not a
  // multiple of the 64-byte beat is split by the XDMA driver into two descriptors that
  // together read one beat more than the FPGA queue holds (FireSim issue #208).  The
  // read then never completes, pread() fails after c2h_timeout (10 s), and the stream
  // is left mis-framed.  See tick() and cpu_managed_stream.cc.
  uint8_t     *dma_buf       = nullptr;
  size_t       dma_buf_bytes = 0;

  // Statistics
  uint64_t     total_records = 0;
  uint64_t     total_beats   = 0;
  bool         discard         = false;
  uint64_t     discarded_beats = 0;

  // [DRAINHIST] Drain measurement (software only; what is written to the bin is unchanged).
  // Per-pull size histogram in beats: bucket 0 = empty pull, bucket k (k >= 1) =
  // [2^(k-1), 2^k) beats; a pull of stream_depth beats (the whole FPGA queue, 512) is
  // also counted in dh_full.  Times are CLOCK_MONOTONIC ns: in pull() (MMIO count read
  // + DMA pread), in the record copy/fwrite loop, and outside this bridge's tick()
  // (other bridges, DONE polling, the scheduler).  Printed once at finish(); with
  // +ift-drain-stats-period=N (seconds, default 0 = off) an interval line every N s.
  static uint64_t dh_now_ns();
  void dh_print_interval(uint64_t now);
  static constexpr int DH_BUCKETS = 34;
  uint64_t     dh_hist[DH_BUCKETS] = {};
  uint64_t     dh_full          = 0;
  uint64_t     dh_max_beats     = 0;
  uint64_t     dh_pull_ns       = 0;
  uint64_t     dh_empty_pull_ns = 0;
  uint64_t     dh_write_ns      = 0;
  uint64_t     dh_outside_ns    = 0;
  uint64_t     dh_ticks         = 0;
  uint64_t     dh_discard_pulls = 0;
  uint64_t     dh_start_ns      = 0;
  uint64_t     dh_last_tick_end_ns = 0;
  uint64_t     dh_run_end_ns    = 0;
  uint64_t     dh_period_ns     = 0;
  uint64_t     dh_next_print_ns = 0;
  // interval snapshot (for the periodic line)
  uint64_t     dh_iv_ns = 0, dh_iv_pulls = 0, dh_iv_beats = 0, dh_iv_full = 0,
               dh_iv_empty = 0, dh_iv_pull_ns = 0, dh_iv_write_ns = 0;
};
