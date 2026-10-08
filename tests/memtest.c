// Baremetal main-memory channel test for the 32 GiB / 2-DDR4-channel Alveo U250 build.
//
// Target DRAM map (ExtMem base 0x8000_0000, size 32 GiB):
//   host channel 0 (DDR bank c0): target phys [0x0_8000_0000 .. 0x4_8000_0000)  (low 16 GiB)
//   host channel 1 (DDR bank c3): target phys [0x4_8000_0000 .. 0x8_8000_0000)  (high 16 GiB)
//
// Writes a self-describing pattern (value == its own address) across an 8 MiB block
// in each region. 8 MiB >> any LLC, so read-back comes from DRAM rather than cache,
// and value==address also catches address-aliasing (e.g. an Option-A routing bug that
// maps channel-1 addresses back onto channel 0). Prints per-region progress so a dead
// channel shows up as a hang right after its "reading back" line.

#include <stdio.h>
#include <stdint.h>

#define MIB    (1024ULL * 1024ULL)
#define BLOCK  (8ULL * MIB)        // per-region footprint; exceeds the LLC

static int test_region(const char *name, uint64_t base) {
  volatile uint64_t *p = (volatile uint64_t *)base;
  uint64_t n = BLOCK / sizeof(uint64_t);

  printf("[memtest] %-9s writing  %lu MiB at 0x%lx ...\n",
         name, (unsigned long)(BLOCK / MIB), (unsigned long)base);
  for (uint64_t i = 0; i < n; i++)
    p[i] = base + i * sizeof(uint64_t);

  printf("[memtest] %-9s reading back ...\n", name);
  for (uint64_t i = 0; i < n; i++) {
    uint64_t addr = base + i * sizeof(uint64_t);
    uint64_t got  = p[i];
    if (got != addr) {
      printf("[memtest] %-9s FAIL @ 0x%lx: expected 0x%lx got 0x%lx\n",
             name, (unsigned long)addr, (unsigned long)addr, (unsigned long)got);
      return 1;
    }
  }
  printf("[memtest] %-9s PASS [0x%lx .. 0x%lx)\n",
         name, (unsigned long)base, (unsigned long)(base + BLOCK));
  return 0;
}

int main(void) {
  int fails = 0;

  // Control: 1 GiB into DRAM -> host channel 0. Must pass; if this fails the test
  // itself / low memory is wrong, not channel c3.
  fails += test_region("chan0",    0x80000000ULL + 1024ULL * MIB);   // 0xC000_0000

  // 16 GiB into DRAM -> first address of host channel 1 (DDR bank c3).
  fails += test_region("chan1-lo", 0x480000000ULL);

  // ~31.5 GiB into DRAM -> near the top of host channel 1.
  fails += test_region("chan1-hi", 0x860000000ULL);

  if (fails == 0)
    printf("[memtest] ALL PASS: 32 GiB verified across both host DRAM channels\n");
  else
    printf("[memtest] %d region(s) FAILED\n", fails);

  return fails;
}
