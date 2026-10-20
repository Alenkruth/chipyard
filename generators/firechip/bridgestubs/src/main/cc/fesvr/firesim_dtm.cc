// See LICENSE for license details
#include "firesim_dtm.h"
#include <inttypes.h>
#include <stdio.h>

#define fprintf(stdout, fmt, ...) (0)

// Defined in firesim_tsi.cc (always linked). Set once the +dmi-no-htif DMI preload
// (Debug Module up, +reconfig_csrs applied) is complete; the TSI fesvr waits on it
// before it writes msip.
extern bool firesim_dmi_preload_done;

firesim_dtm_t::firesim_dtm_t(int argc, char **argv, bool can_have_loadmem)
    : testchip_dtm_t(argc, argv, can_have_loadmem), is_busy(false),
      is_loaded_in_host(false), is_loaded_in_target(false) {
  idle_counts = 10;
  std::vector<std::string> args(argv + 1, argv + argc);
  for (auto &arg : args) {
    if (arg.find("+idle-counts=") == 0)
      idle_counts = atoi(arg.c_str() + 13);
    if (arg == "+dmi-no-htif")
      no_htif = true;
    if (arg.find("+dump-mem=") == 0)
      dump_mem_addr = strtoull(arg.c_str() + 10, nullptr, 0);
  }

  // always use loadmem if the target supports it
  has_loadmem = can_have_loadmem;

  // TSI writes msip in this mode; DMI must not (a second msip / a halt-store-resume
  // on a running program is exactly what +dmi-no-htif exists to avoid).
  if (no_htif)
    write_hart0_msip = false;
}

void firesim_dtm_t::load_program() {
  if (no_htif) {
    // No ELF load (TSI loads it) and no load_symbols(): tohost_addr stays 0, so
    // htif_t::run() takes its `while (!should_exit()) idle();` branch and this
    // fesvr never reads or writes tohost/fromhost.
    printf("[dmi-no-htif] DMI fesvr: no ELF load, no HTIF; TSI owns tohost/fromhost\n");
    fflush(stdout);
    return;
  }
  testchip_dtm_t::load_program();
}

void firesim_dtm_t::idle() {
  // Post-deadlock trap-CSR dump. The bridge sets dump_requested at the trigger
  // cycle; do the halt+read HERE, in the FESVR (host) context where loadarch's
  // identical DMI works -- its abstract commands yield via switch_to_target(),
  // which is valid from this context but not from the bridge tick() (target ctx).
  if (dump_enabled && dump_requested) {
    dump_trap_csrs();
    dump_enabled = false; // one-shot
  }
  is_busy = false;
  for (size_t i = 0; i < idle_counts; i++)
    switch_to_target();
  is_busy = true;
}

// [reconf-fix] Halt hart 0 and dump the machine/supervisor trap CSRs at full
// width. Called from idle() (host/FESVR context) where the DMI abstract commands
// work. Leaves hart 0 halted (per dmibridge contract). NOTE: `printf` (not the
// file-local no-op `fprintf`) so the dump actually prints.
void firesim_dtm_t::dump_trap_csrs() {
  halt(0);
  printf("==== [dump-trap-csrs] hart 0 halted; trap state ====\n");
  printf("  mstatus =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x300));
  printf("  mtvec   =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x305));
  printf("  mepc    =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x341));
  printf("  mcause  =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x342));
  printf("  mtval   =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x343));
  printf("  mip     =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x344));
  printf("  mie     =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x304));
  printf("  mscratch=0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x340));
  printf("  sepc    =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x141));
  printf("  scause  =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x142));
  printf("  stval   =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x143));
  printf("  satp    =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x180));
  printf("  dcsr    =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x7b0));
  printf("  dpc     =0x%016" PRIx64 "\n", (uint64_t)loadarch_read_csr64(0x7b1));
  if (dump_mem_addr != 0) {
    printf("  mem[0x%016" PRIx64 "]=0x%016" PRIx64 "\n", dump_mem_addr,
           dump_read_mem64(dump_mem_addr));
    printf("  mem[0x%016" PRIx64 "]=0x%016" PRIx64 "\n", dump_mem_addr + 8,
           dump_read_mem64(dump_mem_addr + 8));
  }
  printf("==== [dump-trap-csrs] hart 0 left halted ====\n");
  fflush(stdout);
}

// Read one dword through the halted hart's own load path (so the L1 view), via the
// program buffer. s0 is bracketed through dscratch0 like loadarch_read_csr64; the
// hart stays halted. Encodings as in testchip_dtm.cc / dtm.cc.
uint64_t firesim_dtm_t::dump_read_mem64(uint64_t addr) {
  const uint32_t db = get_data_base();
  auto itype = [](uint32_t imm) { return (imm & 0xfff) << 20; };
  uint32_t prog[6] = {
    0x7b241473,                                   // csrrw s0, dscratch0, s0 (save s0)
    0x00003403 | (0u << 15) | itype(db),          // ld s0, data_base(x0)  (addr)
    0x00043403,                                   // ld s0, 0(s0)          (target load)
    0x00803023 | (0u << 15) |                     // sd s0, data_base(x0)  (result)
        ((db & 0x1f) << 7) | (((db >> 5) & 0x7f) << 25),
    0x7b241473,                                   // csrrw s0, dscratch0, s0 (restore)
    0x00100073                                    // ebreak
  };
  uint32_t data[2] = {(uint32_t)addr, (uint32_t)(addr >> 32)};
  // postexec | transfer | write | aarsize=64 | regno x0 (same form as loadarch)
  uint32_t cmd = (1u << 18) | (1u << 17) | (1u << 16) | (3u << 20) | 0x1000;
  uint32_t err = run_abstract_command(cmd, prog, 6, data, 2);
  if (err) {
    printf("  mem read cmderr=%u\n", err);
    return ~0ull;
  }
  return ((uint64_t)read(0x04)) | (((uint64_t)read(0x05)) << 32);   // DM data0/data1
}

void firesim_dtm_t::send_loadmem_word(uint32_t word) {
  loadmem_out_data.push_back(word);
}

void firesim_dtm_t::load_mem_write(addr_t addr,
                                   size_t nbytes,
                                   const void *src) {
  fprintf(stdout,
          "firesim_dtm_t::load_mem_write addr: %" PRIx64 " nbytes: %" PRIu64
          "\n",
          addr,
          nbytes);
  fflush(stdout);

  loadmem_write_reqs.push_back(firesim_loadmem_t(addr, nbytes));
  loadmem_write_data.insert(
      loadmem_write_data.end(), (const char *)src, (const char *)src + nbytes);
}

void firesim_dtm_t::load_mem_read(addr_t addr, size_t nbytes, void *dst) {
  fprintf(stdout,
          "firesim_dtm_t::load_mem_read addr: %" PRIx64 " nbytes: %" PRIu64
          "\n",
          addr,
          nbytes);
  fflush(stdout);

  while (!loadmem_write_reqs.empty())
    switch_to_target();
  loadmem_read_reqs.push_back(firesim_loadmem_t(addr, nbytes));

  uint32_t *result = static_cast<uint32_t *>(dst);
  size_t len = nbytes / sizeof(uint32_t);
  for (size_t i = 0; i < len; i++) {
    while (loadmem_out_data.empty())
      switch_to_target();
    result[i] = loadmem_out_data.front();
    loadmem_out_data.pop_front();
  }
}

void firesim_dtm_t::reset() {
  // after program loading, this function is called and spins until the target
  // thread/bridge has synced/drained all in-flight fesvr xacts
  is_loaded_in_host = true;
  while (!is_loaded_in_target)
    switch_to_target();
  fprintf(
      stdout,
      "firesim_dtm_t::reset done loading program. sending reset signal(s)\n");
  fflush(stdout);
  testchip_dtm_t::reset();
  if (no_htif) {
    // testchip_dtm_t::reset() (no +loadarch) has applied +reconfig_csrs with hart 0
    // halted in the bootrom and resumed it, and skipped msip (write_hart0_msip=false).
    preload_done = true;
    firesim_dmi_preload_done = true;
    printf("[dmi-no-htif] DMI preload done (reconfig applied before program start); "
           "releasing TSI\n");
    fflush(stdout);
  }
}

bool firesim_dtm_t::has_loadmem_reqs() {
  return (!loadmem_write_reqs.empty() || !loadmem_read_reqs.empty());
}

bool firesim_dtm_t::recv_loadmem_write_req(firesim_loadmem_t &loadmem) {
  if (loadmem_write_reqs.empty())
    return false;
  auto r = loadmem_write_reqs.front();
  loadmem.addr = r.addr;
  loadmem.size = r.size;
  loadmem_write_reqs.pop_front();
  return true;
}

bool firesim_dtm_t::recv_loadmem_read_req(firesim_loadmem_t &loadmem) {
  if (loadmem_read_reqs.empty())
    return false;
  auto r = loadmem_read_reqs.front();
  loadmem.addr = r.addr;
  loadmem.size = r.size;
  loadmem_read_reqs.pop_front();
  return true;
}

void firesim_dtm_t::recv_loadmem_data(void *buf, size_t len) {
  std::copy(loadmem_write_data.begin(),
            loadmem_write_data.begin() + len,
            (char *)buf);
  loadmem_write_data.erase(loadmem_write_data.begin(),
                           loadmem_write_data.begin() + len);
}
