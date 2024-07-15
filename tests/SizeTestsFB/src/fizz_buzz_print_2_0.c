#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

#define read_csr(reg)({ unsigned long __tmp; \
    asm volatile ("csrr %0, " #reg : "=r"(__tmp)); \
    __tmp; })

int main() {
    asm volatile("csrwi 0x7c3, 0x4");
    printf("FB CSR set to %ld\n", read_csr(0x7c3));
    long c;
    c = read_csr(cycle);
    int count = 0;
    int incomplete = 1;
    while(incomplete) {
        count += 1;
        if (count == 1000){
            incomplete = 0;
        }
        if (count % 3 == 0){
            printf("Fizz");
        }
        if (count % 5 == 0){
            printf("Buzz");
        }
        printf("\t");
    }
    c = read_csr(cycle) - c;
    printf("%ld total cycles for CSR value of 0x4\n", c);
}
