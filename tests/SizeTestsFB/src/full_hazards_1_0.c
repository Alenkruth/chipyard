#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

#define read_csr(reg)({ unsigned long __tmp; \
    asm volatile ("csrr %0, " #reg : "=r"(__tmp)); \
    __tmp; })

int main() {
    asm volatile("csrwi 0x7c3, 0x2");
    printf("FB CSR set to %ld\n", read_csr(0x7c3));
    long c;
    int a, b, d, e, f, g, h, i, j, k, l, m, n, o, p, q, r, s, t, u, v, w, x, y, z;
    c = read_csr(cycle);
    int count = 0;
    int incomplete = 1;
    s = t = u = v = w = x = y = z = 5;
    while(incomplete) {
        count += 1;
        if (count == 1000){
            incomplete = 0;
        }
        a = s * z - x;
        b = a - w;
        d = b / v + y;
        e = d - a; 
        f = e * b;
        g = f + w;
        h = g / d;
        i = h * a;
        j = i - f;
        k = j + b;
        l = k - j;
        m = l * i + x;
        n = m + g;
        o = n - m;
        p = o * n - m;
        q = p * i / j + c;
        r = q / o;
        s = r  * q;
        t = s - a + b - c + d;
        u = t & g;
        v = s + t + u;
        w = q * r / s + t + u - v;
        x = w * d;
        y = x - y + h;
        z = y + v - g;
    }
    c = read_csr(cycle) - c;
    printf("%i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i, %i\n", a, b, d, e, f, g, h, i, j, k, l, m, n, o, p, q, r, s, t, u, v, w, x, y, z);
    printf("%ld total cycles for CSR value of 0x2\n", c);
}
