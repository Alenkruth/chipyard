#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

#define read_csr(reg)({ unsigned long __tmp; \
    asm volatile ("csrr %0, " #reg : "=r"(__tmp)); \
    __tmp; })

int main() {
    int count = 0;
    //uint64_t cycle = 0xc00;
    while (count < 8) {
        register uint64_t cycles = read_csr(cycle);
        if (cycles % 500 == 0) {
            switch(count) {
            case 0: 
                printf("current value = %ld -- reconfiguring to 1\n", read_csr(0x7c3));
                asm volatile("csrwi 0x7c3, 0x1");
                count++;
                break;
            case 1:
                printf("current value = %ld -- reconfiguring to 2\n", read_csr(0x7c3));
                asm volatile("csrwi 0x7c3, 0x2");
                count++;
                break;
            case 2:
                printf("current value = %ld -- reconfiguring to 3\n", read_csr(0x7c3));
                asm volatile("csrwi 0x7c3, 0x3");
                count++;
                break;
            case 3:
                printf("current value = %ld -- reconfiguring to 4\n", read_csr(0x7c3));
                asm volatile("csrwi 0x7c3, 0x4");
                count++;
                break;
            case 4:
                printf("current value = %ld -- reconfiguring to 5\n", read_csr(0x7c3));
                asm volatile("csrwi 0x7c3, 0x5");
                count++;
                break;
            case 5:
                printf("current value = %ld -- reconfiguring to 6\n", read_csr(0x7c3));
                asm volatile("csrwi 0x7c3, 0x6");
                count++;
                break;
            case 6:
                printf("current value = %ld -- reconfiguring to 7\n", read_csr(0x7c3));
                asm volatile("csrwi 0x7c3, 0x7");
                count++;
                
            case 7:
                printf("current value = %ld -- exiting\n", read_csr(0x7c3));
                exit(0);
            default:
                break;
            }
        }
    }
}