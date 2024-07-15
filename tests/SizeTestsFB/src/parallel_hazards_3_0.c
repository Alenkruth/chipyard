#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

#define read_csr(reg)({ unsigned long __tmp; \
    asm volatile ("csrr %0, " #reg : "=r"(__tmp)); \
    __tmp; })

int main() {
    asm volatile("csrwi 0x7c3, 0x6");
    printf("FB CSR set to %ld\n", read_csr(0x7c3));
    long c;
    int a, b, d, e, f, g, h, i, j, k, l, m, n, o, p, q, r, s, t, u, v, w, x, y, z;
    c = read_csr(cycle);
    int count = 0;
    int incomplete = 1;
    f = k = p = u = z = 5;
    e = j = o = t = y = 2;
    while(incomplete) {
        count += 1;
        if (count == 1000){
            incomplete = 0;
        }
        a = f - e;
        b = a * e;
        d = b / f + a;
        e = d - a; 
        f = e * b;

        g = k + j;
        h = g / j;
        i = h * k;
        j = i - g - h;
        k = j + h;

        l = o * p;
        m = l - o - p;
        n = m + o;
        o = l / n;
        p = o * n - m;

        q = u / t;
        r = q + u;
        s = r + t;
        t = q - r + s;
        u = t + s;

        v = z + y;
        w = v - z;
        x = w + v;
        y = x - w;
        z = y + x;
    }
    c = read_csr(cycle) - c;
    printf("%i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i\n", a, b, d, e, f, g, h, i, j, k, l, m, n, o, p, q, r, s, t, u, v, w, x, y, z);
    printf("%ld total cycles for CSR value of 0x6\n", c);
}
