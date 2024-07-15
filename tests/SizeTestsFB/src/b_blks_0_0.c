#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

#define read_csr(reg)({ unsigned long __tmp; \
    asm volatile ("csrr %0, " #reg : "=r"(__tmp)); \
    __tmp; })

int main() {
    asm volatile("csrwi 0x7c3, 0x0");
    printf("FB CSR set to %ld\n", read_csr(0x7c3));
    unsigned long c1, c2;
    int x[10] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9};
    int y = 0;
    int z[40] = {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
       1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1};
    c1 = read_csr(cycle);
    for (int a = 0; a < 10; a++){
        if (a % 2 == 0) goto b3;
        // basic block 1
        b1:
        while ((x[0] < 10) && (x[3] % 3 == 0)){
            x[0] += x[1];
            x[3] += x[6];
        }
        x[0] = 0;
        x[3] = 3;
        if (a % 2 == 0) goto end;
        // basic block 2
        b2:
        for(int i = 0; i<40; i+=4){
            z[i] += z[39-i];
            z[i-1] += z[39-i+1];
            z[i-2] += z[39-i+2];
            z[i-3] += z[39-i+3];
        }
        y = z[39] - 1;
        if (a % 2 == 0) goto b1;
        // basic block 3
        b3:
        for(int i=0; i<40; i+=10){
            z[i+0] -= y;
            z[i+1] -= y;
            z[i+2] -= y;
            z[i+3] -= y;
            z[i+4] -= y;
            z[i+5] -= y;
            z[i+6] -= y;
            z[i+7] -= y;
            z[i+8] -= y;
            z[i+9] -= y;
        }
        if (a % 2 == 0) goto b2;
        end:
        continue;
    }
    c2 = read_csr(cycle);
    printf("c1: %li, c2: %li, cycles: %li\n", c1, c2, c2 - c1);
    printf("%ld total cycles for CSR value of 0x0\n", c2 - c1);
}