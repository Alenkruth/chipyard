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

private:
  void flush();

  int          stream_idx;
  int          stream_depth;
  unsigned int retire_width;
  unsigned int record_bits;    // always 256
  ClockInfo    clock_info;

  std::string  outfilename;
  FILE        *outfile = nullptr;

  // Statistics
  uint64_t     total_records = 0;
  uint64_t     total_beats   = 0;
};
