// ift_bridge.h — C++ driver for the BOOM IFT FireSim bridge.
//
// Reads 256-bit IFT records from a 512-bit DMA stream and writes them to a
// binary output file.  Each 512-bit beat contains two records (lower 256 bits
// first); a record is valid if its event_type field (bits[1:0]) is non-zero.
//
// Output file format: raw stream of 32-byte (256-bit) IFT record structs.
// The offline analysis tool (parse_ift_log.py or a future ift_decode) maps
// event_type, op_count, influencer entries, etc. from the packed bit layout
// defined in boom.v3.exu.IFTBridgeIO.

#pragma once

#include "core/bridge_driver.h"
#include "core/clock_info.h"

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
  void set_discard(bool on) { discard = on; }
  uint64_t beats_seen() const { return total_beats + discarded_beats; }
  uint64_t discarded() const { return discarded_beats; }

private:
  void flush();

  int          stream_idx;
  int          stream_depth;
  unsigned int retire_width;
  unsigned int record_bits;    // always 256
  ClockInfo    clock_info;

  std::string  outfilename;
  FILE        *outfile    = nullptr;
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
};
