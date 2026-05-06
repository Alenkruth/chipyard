// ift_bridge.cc — BOOM IFT FireSim bridge driver.
// See ift_bridge.h for the stream format and record layout.

#include "ift_bridge.h"

#include <cassert>
#include <cinttypes>
#include <cstdio>
#include <cstring>

char ift_bridge_t::KIND;

// IFT record layout (256 bits = 32 bytes, little-endian):
//   bits[1:0]     event_type  (01=commit, 10=squash, 00=NOP/padding)
//   bits[4:2]     priv
//   bits[44:5]    pc[39:0]
//   bits[76:45]   insn[31:0]
//   bits[84:77]   flags (domain, spec, atk, s_acc, s_prop, s_tx, src_tainted, infl_ovf)
//   bits[100:85]  op_count[15:0]            (16b = uopIDCounterWidthCF)
//   bits[102:101] spec_branch_is_atk, spec_branch_is_secret
//   bits[118:103] spec_branch_op_id[15:0]   (16b = uopIDCounterWidthCF)
//   bits[119]     single_step
//   bits[143:120] fu_bitmap[23:0]
//   bits[227:144] influencer[0..2]          (3 × 28b each)
//     Per slot: valid(1)+op_count(16)+infl_type(5)+is_atk(1)+is_secret(1)+deny_count(4)
//   bits[255:228] reserved/zero
struct ift_record_t {
  uint64_t words[4];  // 4 × 64b = 256b
};
static_assert(sizeof(ift_record_t) == 32, "IFT record must be 32 bytes");

// Extract event_type from a packed record.
static inline int event_type(const ift_record_t &r) {
  return static_cast<int>(r.words[0] & 0x3);
}

ift_bridge_t::ift_bridge_t(simif_t &sim,
                            StreamEngine &stream,
                            int /*wid*/,
                            const std::vector<std::string> &args,
                            int stream_idx,
                            int stream_depth,
                            unsigned int retire_width,
                            unsigned int record_bits,
                            const ClockInfo &clock_info)
    : streaming_bridge_driver_t(sim, stream, &KIND),
      stream_idx(stream_idx),
      stream_depth(stream_depth),
      retire_width(retire_width),
      record_bits(record_bits),
      clock_info(clock_info) {

  outfilename = "";
  const std::string ift_out_arg = "+ift-out=";
  for (const auto &arg : args) {
    if (arg.find(ift_out_arg) == 0) {
      outfilename = arg.substr(ift_out_arg.size());
    }
  }
  if (outfilename.empty()) {
    outfilename = "ift_bridge.bin";
  }
}

ift_bridge_t::~ift_bridge_t() {
  if (outfile) fclose(outfile);
}

void ift_bridge_t::init() {
  outfile = fopen(outfilename.c_str(), "wb");
  if (!outfile) {
    fprintf(stderr, "[IFTBridge] ERROR: cannot open output file '%s'\n",
            outfilename.c_str());
  }
}

void ift_bridge_t::tick() {
  // Each DMA beat is 512 bits = 64 bytes = 2 IFT records of 256 bits each.
  constexpr int RECORDS_PER_BEAT = 2;
  constexpr int BYTES_PER_RECORD = 32;  // 256 bits
  constexpr int BYTES_PER_BEAT   = RECORDS_PER_BEAT * BYTES_PER_RECORD;  // 64

  // pull_stream returns the number of bytes consumed; beats are BYTES_PER_BEAT each.
  // We read in chunks matching the DMA buffer depth.
  const size_t max_bytes = static_cast<size_t>(stream_depth) * BYTES_PER_BEAT;
  std::vector<uint8_t> buf(max_bytes);

  size_t bytes_received = pull(stream_idx, buf.data(), max_bytes, BYTES_PER_BEAT);
  if (bytes_received == 0) return;

  // Truncate to complete beats; a partial beat can arrive when the FPGA resets
  // mid-DMA at simulation teardown — discard the incomplete tail.
  bytes_received -= (bytes_received % BYTES_PER_BEAT);
  const size_t num_beats = bytes_received / BYTES_PER_BEAT;
  total_beats += num_beats;

  if (!outfile) return;

  for (size_t i = 0; i < num_beats; ++i) {
    const uint8_t *beat = buf.data() + i * BYTES_PER_BEAT;
    for (int r = 0; r < RECORDS_PER_BEAT; ++r) {
      ift_record_t rec;
      memcpy(&rec, beat + r * BYTES_PER_RECORD, BYTES_PER_RECORD);
      if (event_type(rec) != 0) {
        fwrite(&rec, BYTES_PER_RECORD, 1, outfile);
        ++total_records;
      }
    }
  }
}

void ift_bridge_t::flush() {
  if (outfile) fflush(outfile);
}

void ift_bridge_t::finish() {
  flush();
  fprintf(stderr, "[IFTBridge] Wrote %" PRIu64 " records (%" PRIu64 " beats) to %s\n",
          total_records, total_beats, outfilename.c_str());
}
