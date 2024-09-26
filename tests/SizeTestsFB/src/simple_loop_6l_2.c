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
    int a, b, c, d, e, f;
    e = 5;
    f = -2;
    c1 = read_csr(cycle);
    for(int i=0; i<1000; i++){
        a = e + f;
        b = f*a;
        c = a & b;
        d = c - b;
        e = d + c;
        f = e/d;
    }
    c2 = read_csr(cycle);
    printf("c1: %li, c2: %li, cycles: %li\n", c1, c2, c2 - c1);
    printf("%ld total cycles for CSR value of 0x0\n", c2 - c1);
}