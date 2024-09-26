#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

#define read_csr(reg)({ unsigned long __tmp; \
    asm volatile ("csrr %0, " #reg : "=r"(__tmp)); \
    __tmp; })

int main() {
    printf("starting main\n");
    asm volatile("csrwi 0x7c3, 0x2");
    printf("FB CSR set to %ld\n", read_csr(0x7c3));
    unsigned long c1, c2;
    int x[10] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9};
    int y = 0;
    int z[40] = {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
       1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1};
    c1 = read_csr(cycle);
    for (int a = 0; a < 40; a++){
            for (int i=0; i<40; i+=4){
                z[i] += 1;
                z[i+1] += 2;
                z[i+2] += 3;
                z[i+3] += 4;
            }
            if(z[3] > 20){
                goto b1;
            }
            goto b2;
        b1:
            for (int i=0; i<40; i+=10) {
                z[i]   = 1;
                z[i+1] = 1;
                z[i+2] = 1;
                z[i+3] = 1;
                z[i+4] = 1;
                z[i+5] = 1;
                z[i+6] = 1;
                z[i+7] = 1;
                z[i+8] = 1;
                z[i+9] = 1;
            }
        b2:
            if(y % 2 == 0) goto b3;
            for (int i=0; i<40; i+=2){
                z[i] -= 2;
                z[i+1] -= 2;
            }
            goto end;
        b3:
            for (int i=0; i<40; i+=5){
                z[i]   *= 2;
                z[i+1] *= 2;
                z[i+2] *= 2;
                z[i+3] *= 2;
                z[i+4] *= 2;
            }
        end:
            y = a;
    }
    c2 = read_csr(cycle);
    printf("c1: %li, c2: %li, cycles: %li\n", c1, c2, c2 - c1);
    printf("%ld total cycles for CSR value of 0x2\n", c2 - c1);
}