// See LICENSE for license details
#ifndef __FIRESIM_DTM_H
#define __FIRESIM_DTM_H

#include "testchip_dtm.h"

struct firesim_loadmem_t {
  firesim_loadmem_t() : addr(0), size(0) {}
  firesim_loadmem_t(size_t addr, size_t size) : addr(addr), size(size) {}
  size_t addr;
  size_t size;
};

class firesim_dtm_t final : public testchip_dtm_t {
public:
  firesim_dtm_t(int argc, char **argv, bool can_have_loadmem);
  ~firesim_dtm_t() {}

  bool busy() { return is_busy; };
  bool loaded_in_host() { return is_loaded_in_host; };
  void set_loaded_in_target(bool loaded) { is_loaded_in_target = loaded; };

  bool recv_loadmem_write_req(firesim_loadmem_t &loadmem);
  bool recv_loadmem_read_req(firesim_loadmem_t &loadmem);
  void recv_loadmem_data(void *buf, size_t len);
  bool has_loadmem_reqs();

  void send_loadmem_word(uint32_t word);

  // +dmi-no-htif: the DMI fesvr loads nothing and never touches tohost/fromhost.
  // It only brings up the Debug Module, applies +reconfig_csrs (hart halted in the
  // bootrom), then releases the TSI fesvr to start hart 0 (firesim_dmi_preload_done).
  // TSI owns the ELF load, msip and all HTIF. htif_parked() lets dmibridge stop
  // issuing DMI requests once that is done (unless a trap dump is pending).
  bool htif_parked() { return no_htif && preload_done; }
  void load_program() override;

  // [reconf-fix] Post-deadlock trap-CSR dump (dmibridge +dump-at-cycle). Public
  // so dmibridge_t sets them via fesvr->. One-shot: idle() dumps when both set.
  bool dump_enabled = false;
  bool dump_requested = false;

protected:
  void idle() override;

  void reset() override;

  void load_mem_write(addr_t addr, size_t nbytes, const void *src) override;
  void load_mem_read(addr_t addr, size_t nbytes, void *dst) override;

  // This must be exactly the same as hKey.dataBits in the LoadMem widget
  // See discussion here: https://github.com/firesim/firesim/pull/1401
  // TODO: Avoid hardcoding this value here
  size_t chunk_align() override { return 8; }

  std::deque<firesim_loadmem_t> loadmem_write_reqs;
  std::deque<firesim_loadmem_t> loadmem_read_reqs;
  std::deque<char> loadmem_write_data;

  std::deque<uint32_t> loadmem_out_data;

private:
  size_t idle_counts;
  bool no_htif = false;
  bool preload_done = false;
  // +dump-mem=<addr>: the trap dump also reads the two dwords at addr, addr+8 as
  // the HART sees them (e.g. tohost/fromhost), to compare with what TSI sees.
  uint64_t dump_mem_addr = 0;
  uint64_t dump_read_mem64(uint64_t addr);
  // Halt hart 0 and print the machine/supervisor trap CSRs (leaves it halted).
  void dump_trap_csrs();
  bool is_busy;
  // program load has completed in the host thread (i.e. all fesvr xacts for
  // program load have been sent by fesvr)
  bool is_loaded_in_host;
  // program load has completed in the target thread (i.e. all in-flight fesvr
  // xacts for program load have been synced/drained by the target
  // thread/bridge)
  bool is_loaded_in_target;
};
#endif // __FIRESIM_DTM_H
