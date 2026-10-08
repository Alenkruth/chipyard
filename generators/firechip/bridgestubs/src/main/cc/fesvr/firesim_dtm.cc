// See LICENSE for license details
#include "firesim_dtm.h"
#include <inttypes.h>
#include <stdio.h>

#define fprintf(stdout, fmt, ...) (0)

firesim_dtm_t::firesim_dtm_t(int argc, char **argv, bool can_have_loadmem)
    : testchip_dtm_t(argc, argv, can_have_loadmem), is_busy(false),
      is_loaded_in_host(false), is_loaded_in_target(false) {
  idle_counts = 10;
  std::vector<std::string> args(argv + 1, argv + argc);
  for (auto &arg : args) {
    if (arg.find("+idle-counts=") == 0)
      idle_counts = atoi(arg.c_str() + 13);
  }

  // always use loadmem if the target supports it
  has_loadmem = can_have_loadmem;
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
  printf("==== [dump-trap-csrs] hart 0 left halted ====\n");
  fflush(stdout);
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
