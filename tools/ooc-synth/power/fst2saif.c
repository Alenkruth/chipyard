/*
 * fst2saif.c -- FST dump -> SAIF (switching activity) for one scope and one clock-cycle window.
 *
 * Purpose: activity-based power pilot (claude-artifacts/ooc-synthesis/plans/power-pilot-plan.md, 1.3).
 *   Reads a Verilator FST dump with the FST reader bundled with Verilator, keeps the variables below
 *   --scope, and writes a SAIF with T0/T1/TX/TC per bit accumulated only inside the window
 *   [t(A), t(A+N)), where t(k) is the time of the k-th rising edge (k = 0 is the first) of --clock.
 *
 * Definitions (the reference fst2saif_ref.py implements exactly these):
 *   - rising edge: clock value changes from '0' to '1'.  Window = [t(A), t(A+N)); a change exactly at
 *     t(A) is inside, a change exactly at t(A+N) is outside.  The clock therefore has TC = 2N.
 *   - bit values: '0' and '1'; every other FST value character (x z h u w l -) counts as X.
 *   - TC counts changes between '0' and '1' only (a change into or out of X is not a toggle).
 *   - bits not yet dumped at window start are X (Verilator dumps every value at time 0).
 *   - time scaling: x_out = round_half_up(x * out_ns * N / (t(A+N) - t(A))), so the N simulated cycles
 *     map to DURATION = N * out_ns (SAIF time unit 1 ns).  T1 and TX are rounded, TX is clamped to
 *     DURATION - T1, T0 = DURATION - T1 - TX, so T0+T1+TX = DURATION.  TC is not scaled.
 *   - bus bits are written as escaped per-bit names  name\[i\] ; the Verilator name suffix " [msb:lsb]"
 *     is removed from the base name.  Aliases (several names on one FST handle) share one state and
 *     each name is written.  Unpacked arrays (names whose base ends in [digits]) are skipped and counted
 *     unless --arrays is given; real/string/event variables are always skipped and counted.  SAIF instances
 *     mirror the FST scopes from the root down to the tile (scopes above --scope carry no nets); the divider is "/".
 *   - --arrays: an unpacked-array element "Memory[3] [79:0]" is written like a bus of base "Memory[3]", i.e. as
 *     Memory\[3\]\[79\] ... Memory\[3\]\[0\]; a 1-bit element "v[3]" as v\[3\].  Verilator traces only arrays up
 *     to --trace-max-array entries (default 32); larger arrays are absent from the dump (see --mem-from-ports).
 *   - --mem-from-ports REL[:DEPTH]: the array "Memory" of the memory module at scope --scope/REL (a firtool *_ext module
 *     whose contents are not in the dump) is rebuilt from its traced write ports W<k>_en/_addr/_data[/_mask]:
 *     at every rising edge e of --clock (each W<k>_clk must be an alias of it), with the port values held just
 *     before e (changes at time e are applied after the write), for k = 0,1,... in order: if en is '1' and addr is
 *     all 0/1 and < DEPTH (default 2^width(addr); give the RTL depth, e.g. reg [63:0] Memory[0:191] -> :192), word addr takes data (with a mask of m bits, lane i = data bits
 *     [i*w/m +: w/m] is written when mask[i] is '1', set to X when mask[i] is X, kept when '0'); a later port
 *     overrides an earlier one in the same edge; the word changes once, at time e (X bits allowed, as for dumped
 *     bits).  Words start X, so a word never written stays X (TX = DURATION, TC = 0).  All DEPTH words are written
 *     to the SAIF, as nets Memory\[w\]\[b\] (w ascending, bits msb first) after the scope's own nets.  Their
 *     toggles count in the profile (cycle = index of edge e) and in mem_tc, not in total_tc.
 *
 * Inputs : --fst F --scope PATH --clock NAME --from-cycle A --cycles N [--out-period-ns P] --out X.saif
 *          PATH uses '/' between scope names, exactly as printed by --list-scopes (e.g. TOP/TestDriver/...).
 *          NAME is the clock variable name relative to --scope (1 bit, e.g. "clock").
 * Options: --out-period-ns P  output cycle length in ns, positive integer (default 10)
 *          --skip-underscore  drop variables whose base name starts with '_'
 *          --arrays           emit unpacked-array elements instead of skipping them (see above)
 *          --mem-from-ports REL[:DEPTH]  rebuild REL's "Memory" from its write ports (repeatable, at most 16; see above)
 *          --profile X.csv --bucket B   per-bucket toggle totals (all kept bits) for cycles 0..A+N-1 (B default 1000);
 *                                       csv header: bucket,first_cycle,toggles
 *          --list-scopes      print "<scope path><TAB><direct variables>" for every scope and exit
 * Build  : V=/home/jht9sy/work/chipyard/.conda-env/share/verilator/include/gtkwave
 *          gcc -O2 -DFST_CONFIG_INCLUDE='"fst_config.h"' -o fst2saif fst2saif.c $V/fstapi.c $V/lz4.c $V/fastlz.c -I$V -lz -lpthread -lm
 *          (-DFST_CONFIG_INCLUDE is needed: fstapi.c otherwise includes a configure-generated <config.h>)
 * Output : SAIF at --out, summary lines "key=value" on stdout (variables, bits, aliases, arrays_skipped,
 *          others_skipped, total_tc, edges, window_start_time, window_end_time,
 *          period_time (clock period in FST time units), timescale_exp (FST timescale, power of ten of seconds),
 *          period_ns (clock period in ns), wall_s, cpu_s, peak_rss_kb; with --arrays also arrays_emitted, with
 *          --mem-from-ports also mem_words (words in the SAIF), mem_words_written, mem_bits, mem_tc).
 * Exit   : 0 ok, 2 usage, 3 data problem (scope/clock missing, dump ends before the window end).
 * Two passes over the dump: pass 1 reads only the clock (edge times), pass 2 the kept variables up to the window end.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <ctype.h>
#include <math.h>
#include <sys/resource.h>
#include "fstapi.h"

typedef unsigned __int128 u128;

typedef struct { char *base; int msb, lsb, bussed, nbits; fstHandle h; } Var;
typedef struct Node { char *name; struct Node *kid, *next, *last; int *vars; int nv, cap, mem; } Node;
typedef struct { char *path; int nvars; } ScopeRec;

static Var *vars; static size_t nvars, capvars;
static ScopeRec *scopes; static size_t nscopes, capscopes;

static void die(int rc, const char *m, const char *a) { fprintf(stderr, "fst2saif: %s%s%s\n", m, a ? ": " : "", a ? a : ""); exit(rc); }
static void *xrealloc(void *p, size_t n) { p = realloc(p, n); if (!p) die(3, "out of memory", NULL); return p; }
static double now_s(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

/* split "base [msb:lsb]" -> base (in place), returns 1 and msb/lsb when the suffix is present */
static int split_range(char *name, int *msb, int *lsb) {
    size_t n = strlen(name);
    if (n > 4 && name[n - 1] == ']') {
        char *p = strrchr(name, '[');
        if (p && p > name && p[-1] == ' ') {
            int a, b; char c;
            if (sscanf(p, "[%d:%d%c", &a, &b, &c) == 3 && c == ']') { *msb = a; *lsb = b; p[-1] = 0; return 1; }
        }
    }
    return 0;
}
/* unpacked-array element: base name ends in [digits] */
static int is_array_elem(const char *base) {
    size_t n = strlen(base);
    if (n < 3 || base[n - 1] != ']') return 0;
    size_t i = n - 2; int d = 0;
    while (i > 0 && isdigit((unsigned char)base[i])) { i--; d++; }
    return d > 0 && base[i] == '[';
}

static Node *node_new(const char *name) { Node *n = calloc(1, sizeof *n); n->name = strdup(name); return n; }
static Node *node_kid(Node *p, const char *name) {
    for (Node *k = p->kid; k; k = k->next) if (!strcmp(k->name, name)) return k;
    Node *k = node_new(name);
    if (p->last) p->last->next = k; else p->kid = k;
    p->last = k; return k;
}

/* ---------------- state for pass 2 ---------------- */
static uint64_t *Tt;      /* 3 per bit: T0 T1 TX */
static uint64_t *TC;      /* 1 per bit */
static char *cur;         /* current value per bit */
static int64_t *hoff;     /* per handle: first bit index or -1 */
static int32_t *hlen;     /* per handle: bits */
static uint64_t *hlt;     /* per handle: last change time */
static uint64_t t0, t1;
static uint64_t *edges; static size_t nedges, capedges, need_edges;
static fstHandle clockh; static char clockprev = 'x';
static uint64_t *prof; static size_t nprof; static uint64_t bucket = 1000; static size_t ecur;
static uint64_t total_tc_window;

/* --mem-from-ports: memories rebuilt from their write ports (bits stored after the dumped bits in Tt/TC/cur) */
#define MAXMEM 16
#define MAXPORT 16
typedef struct {
    char *rel; Node *node; int np;
    fstHandle en[MAXPORT], addr[MAXPORT], data[MAXPORT], mask[MAXPORT], clk[MAXPORT];
    int aw[MAXPORT], dwp[MAXPORT], mw[MAXPORT];
    int dw; uint32_t depth, rdepth; int64_t base; uint64_t *wlt; uint8_t *written; char *pend; uint32_t *tw;
} Mem;
static Mem mems[MAXMEM]; static int nmems; static size_t eproc; static uint64_t mem_tc;
static void mem_commit(Mem *m, uint32_t a, const char *pv, uint64_t e, size_t j) {
    int64_t off = m->base + (int64_t)a * m->dw; uint64_t lo = m->wlt[a] > t0 ? m->wlt[a] : t0; int inwin = e >= t0;
    if (inwin && e > lo) { uint64_t d = e - lo; for (int i = 0; i < m->dw; i++) { char c = cur[off + i]; Tt[3 * (off + i) + (c == '0' ? 0 : c == '1' ? 1 : 2)] += d; } }
    uint64_t tog = 0;
    for (int i = 0; i < m->dw; i++) {
        char o = cur[off + i], nv = pv[i];
        if ((o == '0' && nv == '1') || (o == '1' && nv == '0')) { tog++; if (inwin) TC[off + i]++; }
        cur[off + i] = nv;
    }
    m->wlt[a] = e; m->written[a] = 1;
    if (inwin) mem_tc += tog;
    if (prof && tog) { size_t b = j / bucket; if (b < nprof) prof[b] += tog; }
}
static void mem_edge(uint64_t e, size_t j) {
    for (int mi = 0; mi < nmems; mi++) {
        Mem *m = &mems[mi]; int nt = 0;
        for (int k = 0; k < m->np; k++) {
            if (cur[hoff[m->en[k]]] != '1') continue;
            uint64_t a = 0; int ok = 1; int64_t ao = hoff[m->addr[k]];
            for (int i = 0; i < m->aw[k]; i++) { char c = cur[ao + i]; if (c == '1') a = a * 2 + 1; else if (c == '0') a = a * 2; else ok = 0; }
            if (!ok || a >= m->depth) continue;
            int t = 0; while (t < nt && m->tw[t] != a) t++;
            char *pv = m->pend + (size_t)t * m->dw;
            if (t == nt) { m->tw[nt++] = (uint32_t)a; memcpy(pv, cur + m->base + (int64_t)a * m->dw, (size_t)m->dw); }
            int64_t dof = hoff[m->data[k]];
            int lw = m->mw[k] ? m->dw / m->mw[k] : m->dw; int64_t mo = m->mw[k] ? hoff[m->mask[k]] : -1;
            for (int i = 0; i < m->dw; i++) {   /* i = position in the value string, msb first */
                if (mo >= 0) { int lane = (m->dw - 1 - i) / lw; char mc = cur[mo + (m->mw[k] - 1 - lane)];
                               if (mc == '0') continue;
                               if (mc != '1') { pv[i] = 'x'; continue; } }
                pv[i] = cur[dof + i];
            }
        }
        for (int t = 0; t < nt; t++) mem_commit(m, m->tw[t], m->pend + (size_t)t * m->dw, e, j);
    }
}
/* write edges e <= time that lie before the window end, in edge order */
static void mem_edges_upto(uint64_t time) { while (eproc < nedges && edges[eproc] <= time && edges[eproc] < t1) { mem_edge(edges[eproc], eproc); eproc++; } }

static void cb_clock(void *u, uint64_t time, fstHandle h, const unsigned char *v) {
    (void)u;
    if (h != clockh) return;
    char c = (char)v[0];
    if (c == '1' && clockprev == '0' && nedges < need_edges) {
        if (nedges == capedges) { capedges = capedges ? capedges * 2 : 1 << 16; edges = xrealloc(edges, capedges * 8); }
        edges[nedges++] = time;
    }
    clockprev = (c == '0' || c == '1') ? c : 'x';
}

static void cb_vc(void *u, uint64_t time, fstHandle h, const unsigned char *v) {
    (void)u;
    if (nmems) mem_edges_upto(time);
    if (time >= t1) return;
    int64_t off = hoff[h]; if (off < 0) return;
    int n = hlen[h];
    uint64_t lo = hlt[h] > t0 ? hlt[h] : t0;
    int inwin = time >= t0;
    if (inwin && time > lo) { uint64_t d = time - lo; for (int i = 0; i < n; i++) { char c = cur[off + i]; Tt[3 * (off + i) + (c == '0' ? 0 : c == '1' ? 1 : 2)] += d; } }
    uint64_t tog = 0;
    for (int i = 0; i < n; i++) {
        char nv = (char)v[i]; if (nv != '0' && nv != '1') nv = 'x';
        char o = cur[off + i];
        if ((o == '0' && nv == '1') || (o == '1' && nv == '0')) { tog++; if (inwin) TC[off + i]++; }
        cur[off + i] = nv;
    }
    hlt[h] = time;
    if (inwin) total_tc_window += tog;
    if (prof && tog) {
        while (ecur + 1 < nedges && edges[ecur + 1] <= time) ecur++;
        uint64_t cyc = (nedges && edges[0] <= time) ? ecur : 0;
        size_t b = cyc / bucket; if (b < nprof) prof[b] += tog;
    }
}

/* ---------------- SAIF writer ---------------- */
static FILE *out;
static void esc(const char *s) { for (; *s; s++) { if (!isalnum((unsigned char)*s) && *s != '_') fputc('\\', out); fputc(*s, out); } }
static uint64_t scale_div(uint64_t x, uint64_t outns, uint64_t N, uint64_t den) {
    u128 num = (u128)x * outns * N; u128 d = den;
    return (uint64_t)((num * 2 + d) / (2 * d));
}
static uint64_t DUR, OUTNS, NCYC, WDEN;
static void ind(int d) { for (int i = 0; i < d; i++) fputc(' ', out); }
static void net(int depth, const char *base, const char *idx, int64_t bit) {
    uint64_t T0 = Tt[3 * bit], T1 = Tt[3 * bit + 1], TX = Tt[3 * bit + 2];
    (void)T0;
    uint64_t s1 = scale_div(T1, OUTNS, NCYC, WDEN), sx = scale_div(TX, OUTNS, NCYC, WDEN);
    if (s1 > DUR) s1 = DUR;
    if (s1 + sx > DUR) sx = DUR - s1;
    uint64_t s0 = DUR - s1 - sx;
    ind(depth); fputc('(', out); esc(base); if (idx) { fputs("\\[", out); fputs(idx, out); fputs("\\]", out); }
    fprintf(out, " (T0 %llu) (T1 %llu) (TX %llu) (TC %llu) (IG 0))\n", (unsigned long long)s0, (unsigned long long)s1, (unsigned long long)sx, (unsigned long long)TC[bit]);
}
static int has_nets(Node *n) { if (n->nv) return 1; for (Node *k = n->kid; k; k = k->next) if (has_nets(k)) return 1; return 0; }
static void write_node(Node *n, int depth) {
    if (!has_nets(n)) return;
    ind(depth);
    fputs("(INSTANCE ", out); esc(n->name); fputc('\n', out);
    if (n->nv) {
        ind(depth + 1); fputs("(NET\n", out);
        for (int j = 0; j < n->nv; j++) {
            Var *v = &vars[n->vars[j]]; int64_t off = hoff[v->h];
            if (!v->bussed && v->nbits == 1) net(depth + 2, v->base, NULL, off);
            else for (int p = 0; p < v->nbits; p++) {
                int idx = v->bussed ? (v->msb >= v->lsb ? v->msb - p : v->msb + p) : v->nbits - 1 - p;
                char b[32]; snprintf(b, sizeof b, "%d", idx);
                net(depth + 2, v->base, b, off + p);
            }
        }
        if (n->mem) {
            Mem *m = &mems[n->mem - 1];
            for (uint32_t w = 0; w < m->depth; w++) {
                char wb[48], b[32]; snprintf(wb, sizeof wb, "Memory[%u]", w);
                for (int p = 0; p < m->dw; p++) { snprintf(b, sizeof b, "%d", m->dw - 1 - p); net(depth + 2, wb, b, m->base + (int64_t)w * m->dw + p); }
            }
        }
        ind(depth + 1); fputs(")\n", out);
    }
    for (Node *k = n->kid; k; k = k->next) write_node(k, depth + 1);
    ind(depth);
    fputs(")\n", out);
}

static int usage(void) { fputs("usage: fst2saif --fst F --scope PATH --clock NAME --from-cycle A --cycles N --out X.saif [--out-period-ns P] [--skip-underscore] [--arrays] [--mem-from-ports REL[:DEPTH]]... [--profile X.csv --bucket B] | --fst F --list-scopes\n", stderr); return 2; }

int main(int argc, char **argv) {
    const char *fst = NULL, *scope = NULL, *clockname = NULL, *outp = NULL, *profp = NULL;
    uint64_t A = 0, N = 0; int haveA = 0, list = 0, skipu = 0, arrays = 0; OUTNS = 10;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        #define ARG (i + 1 < argc ? argv[++i] : (usage(), exit(2), (char *)0))
        if (!strcmp(a, "--fst")) fst = ARG; else if (!strcmp(a, "--scope")) scope = ARG;
        else if (!strcmp(a, "--clock")) clockname = ARG; else if (!strcmp(a, "--out")) outp = ARG;
        else if (!strcmp(a, "--from-cycle")) { A = strtoull(ARG, NULL, 10); haveA = 1; }
        else if (!strcmp(a, "--cycles")) N = strtoull(ARG, NULL, 10);
        else if (!strcmp(a, "--out-period-ns")) OUTNS = strtoull(ARG, NULL, 10);
        else if (!strcmp(a, "--profile")) profp = ARG; else if (!strcmp(a, "--bucket")) bucket = strtoull(ARG, NULL, 10);
        else if (!strcmp(a, "--arrays")) arrays = 1;
        else if (!strcmp(a, "--mem-from-ports")) { if (nmems == MAXMEM) die(2, "too many --mem-from-ports", NULL); Mem *m = &mems[nmems++]; m->rel = strdup(ARG);
            char *c = strrchr(m->rel, ':'); if (c) { char *e; unsigned long d = strtoul(c + 1, &e, 10); if (*e || !d || d > (1ul << 24)) die(2, "bad :DEPTH in --mem-from-ports", c); m->rdepth = (uint32_t)d; *c = 0; } }
        else if (!strcmp(a, "--skip-underscore")) skipu = 1; else if (!strcmp(a, "--list-scopes")) list = 1;
        else return usage();
    }
    if (!fst) return usage();
    if (!list && (!scope || !clockname || !haveA || !N || !outp || !OUTNS || !bucket)) return usage();
    double w0 = now_s();
    void *ctx = fstReaderOpen(fst); if (!ctx) die(3, "cannot open FST", fst);

    /* hierarchy */
    char path[16384]; size_t plen = 0; size_t stk[4096]; int sp = 0;
    size_t slen = scope ? strlen(scope) : 0;
    Node *nstack[4096]; nstack[0] = NULL;
    size_t arrays_skipped = 0, arrays_emitted = 0, others_skipped = 0, aliases = 0; int64_t clockfound = 0; fstHandle maxh = fstReaderGetMaxHandle(ctx);
    int32_t *hlen_local = calloc((size_t)maxh + 2, sizeof(int32_t)); int8_t *hkept = calloc((size_t)maxh + 2, 1);
    /* kept var -> hierarchy node is attached while walking: nodes below `scope` are created on demand */
    Node *tilenode = NULL; size_t cur_scope_idx[4096];
    struct fstHier *h;
    while ((h = fstReaderIterateHier(ctx))) {
        if (h->htyp == FST_HT_SCOPE) {
            size_t nl = h->u.scope.name_length;
            if (plen + nl + 2 >= sizeof path) die(3, "scope path too long", NULL);
            stk[sp] = plen;
            if (plen) path[plen++] = '/';
            memcpy(path + plen, h->u.scope.name, nl); plen += nl; path[plen] = 0;
            if (scopes == NULL || nscopes == capscopes) { capscopes = capscopes ? capscopes * 2 : 1024; scopes = xrealloc(scopes, capscopes * sizeof *scopes); }
            scopes[nscopes].path = strdup(path); scopes[nscopes].nvars = 0; cur_scope_idx[sp] = nscopes; nscopes++;
            if (scope) {
                int inside = (plen >= slen && !strncmp(path, scope, slen) && (plen == slen || path[slen] == '/'));
                if (plen == slen && inside) { tilenode = node_new(h->u.scope.name); nstack[sp + 1] = tilenode; }
                else if (inside) nstack[sp + 1] = node_kid(nstack[sp], h->u.scope.name);
                else nstack[sp + 1] = NULL;
                if (inside && plen > slen) for (int mi = 0; mi < nmems; mi++)
                    if (!strcmp(path + slen + 1, mems[mi].rel)) { if (mems[mi].node) die(3, "memory scope appears twice", mems[mi].rel); mems[mi].node = nstack[sp + 1]; nstack[sp + 1]->mem = mi + 1; }
            }
            sp++;
        } else if (h->htyp == FST_HT_UPSCOPE) {
            if (sp > 0) { sp--; plen = stk[sp]; path[plen] = 0; }
        } else if (h->htyp == FST_HT_VAR) {
            if (sp > 0) scopes[cur_scope_idx[sp - 1]].nvars++;
            if (!scope || sp == 0 || !nstack[sp]) continue;
            int typ = h->u.var.typ;
            if (typ == FST_VT_VCD_REAL || typ == FST_VT_VCD_REAL_PARAMETER || typ == FST_VT_VCD_REALTIME || typ == FST_VT_SV_SHORTREAL || typ == FST_VT_GEN_STRING || typ == FST_VT_VCD_EVENT) { others_skipped++; continue; }
            char *nm = strndup(h->u.var.name, h->u.var.name_length);
            int msb = 0, lsb = 0, bussed = split_range(nm, &msb, &lsb);
            int is_clock = 0;
            { size_t cl = strlen(clockname); is_clock = !strcmp(nm, clockname) && plen == slen && cl > 0; (void)cl; }
            /* clock is a direct child of the tile scope; the relative name may also contain '/' for deeper variables */
            if (!is_clock && clockname && strchr(clockname, '/')) {
                size_t rl = plen - slen; /* path = scope + '/' + rel */
                if (plen > slen) { char full[16384]; snprintf(full, sizeof full, "%s/%s", path + slen + 1, nm); is_clock = !strcmp(full, clockname); (void)rl; }
            }
            if (!arrays && is_array_elem(nm)) { arrays_skipped++; free(nm); continue; }
            if (arrays && is_array_elem(nm)) arrays_emitted++;
            if (nstack[sp]->mem) {   /* write-port variables of a --mem-from-ports memory */
                Mem *m = &mems[nstack[sp]->mem - 1]; int k; char f[16], c;
                if (sscanf(nm, "W%d_%15[a-z]%c", &k, f, &c) == 2 && k >= 0 && k < MAXPORT) {
                    fstHandle hh = h->u.var.handle; int len = (int)h->u.var.length;
                    if (k + 1 > m->np) m->np = k + 1;
                    if (!strcmp(f, "en")) { if (len != 1) die(3, "write enable is not 1 bit", nm); m->en[k] = hh; }
                    else if (!strcmp(f, "addr")) { m->addr[k] = hh; m->aw[k] = len; }
                    else if (!strcmp(f, "data")) { m->data[k] = hh; m->dwp[k] = len; }
                    else if (!strcmp(f, "mask")) { m->mask[k] = hh; m->mw[k] = len; }
                    else if (!strcmp(f, "clk")) m->clk[k] = hh;
                }
            }
            if (skipu && nm[0] == '_' && !is_clock) { free(nm); continue; }
            if (is_clock) { if (h->u.var.length != 1) die(3, "clock is not 1 bit", nm); clockh = h->u.var.handle; clockfound = 1; }
            if (nvars == capvars) { capvars = capvars ? capvars * 2 : 4096; vars = xrealloc(vars, capvars * sizeof *vars); }
            Var *v = &vars[nvars];
            v->base = nm; v->msb = msb; v->lsb = lsb; v->bussed = bussed; v->nbits = (int)h->u.var.length; v->h = h->u.var.handle;
            if (hkept[v->h]) aliases++; else { hkept[v->h] = 1; hlen_local[v->h] = v->nbits; }
            Node *nd = nstack[sp];
            if (nd->nv == nd->cap) { nd->cap = nd->cap ? nd->cap * 2 : 8; nd->vars = xrealloc(nd->vars, nd->cap * sizeof(int)); }
            nd->vars[nd->nv++] = (int)nvars; nvars++;
        }
    }
    if (list) {
        for (size_t i = 0; i < nscopes; i++) printf("%s\t%d\n", scopes[i].path, scopes[i].nvars);
        printf("# scopes=%zu\n", nscopes); fstReaderClose(ctx); return 0;
    }
    if (!tilenode) die(3, "scope not found in FST (see --list-scopes)", scope);
    if (!clockfound) die(3, "clock variable not found under scope", clockname);
    if (N == 0 || A + N < A) die(2, "bad --cycles", NULL);
    for (int mi = 0; mi < nmems; mi++) {
        Mem *m = &mems[mi];
        if (!m->node) die(3, "--mem-from-ports scope not found below --scope", m->rel);
        if (m->np == 0) die(3, "no write port W<k>_* in memory scope", m->rel);
        m->dw = m->dwp[0];
        for (int k = 0; k < m->np; k++) {
            if (!m->en[k] || !m->addr[k] || !m->data[k] || !m->clk[k]) die(3, "write port lacks en/addr/data/clk", m->rel);
            if (m->clk[k] != clockh) die(3, "write-port clock is not the --clock variable", m->rel);
            if (m->dwp[k] != m->dw || m->aw[k] != m->aw[0] || m->aw[k] < 1 || m->aw[k] > 24) die(3, "write ports differ in width or address too wide", m->rel);
            if (m->mw[k] && m->dw % m->mw[k]) die(3, "data width is not a multiple of the mask width", m->rel);
        }
        m->depth = 1u << m->aw[0];
        if (m->rdepth) { if (m->rdepth > m->depth) die(3, "DEPTH exceeds 2^width(addr)", m->rel); m->depth = m->rdepth; }
    }

    /* pass 1: clock edges */
    need_edges = (size_t)(A + N + 1);
    fstReaderClrFacProcessMaskAll(ctx); fstReaderSetFacProcessMask(ctx, clockh);
    fstReaderIterBlocks2(ctx, cb_clock, NULL, NULL, NULL);
    if (nedges < need_edges) { fprintf(stderr, "fst2saif: dump holds %zu rising edges of the clock, window needs %zu\n", nedges, need_edges); return 3; }
    t0 = edges[A]; t1 = edges[A + N]; WDEN = t1 - t0; NCYC = N; DUR = N * OUTNS;
    if (WDEN == 0) die(3, "zero-length window", NULL);

    /* pass 2: kept variables */
    size_t nbits = 0; hoff = malloc(((size_t)maxh + 2) * sizeof(int64_t)); hlen = hlen_local; hlt = calloc((size_t)maxh + 2, 8);
    for (fstHandle k = 0; k <= maxh + 1; k++) hoff[k] = -1;
    for (fstHandle k = 1; k <= maxh; k++) if (hkept[k]) { hoff[k] = (int64_t)nbits; nbits += (size_t)hlen[k]; }
    size_t nall = nbits;
    for (int mi = 0; mi < nmems; mi++) { Mem *m = &mems[mi]; m->base = (int64_t)nall; nall += (size_t)m->depth * m->dw;
        m->wlt = calloc(m->depth, 8); m->written = calloc(m->depth, 1); m->pend = malloc((size_t)m->np * m->dw); m->tw = malloc((size_t)m->np * 4); }
    Tt = calloc(nall * 3, 8); TC = calloc(nall ? nall : 1, 8); cur = malloc(nall ? nall : 1); memset(cur, 'x', nall);
    if (profp) { nprof = (size_t)((A + N + bucket - 1) / bucket); prof = calloc(nprof, 8); }
    fstReaderClrFacProcessMaskAll(ctx);
    for (fstHandle k = 1; k <= maxh; k++) if (hkept[k]) fstReaderSetFacProcessMask(ctx, k);
    fstReaderSetLimitTimeRange(ctx, fstReaderGetStartTime(ctx), t1);
    fstReaderIterBlocks2(ctx, cb_vc, NULL, NULL, NULL);
    if (nmems) mem_edges_upto(t1);
    for (fstHandle k = 1; k <= maxh; k++) if (hkept[k]) {
        int64_t off = hoff[k]; uint64_t lo = hlt[k] > t0 ? hlt[k] : t0;
        if (t1 > lo) for (int i = 0; i < hlen[k]; i++) { char c = cur[off + i]; Tt[3 * (off + i) + (c == '0' ? 0 : c == '1' ? 1 : 2)] += t1 - lo; }
    }
    size_t mem_words = 0, mem_written = 0, mem_bits = 0;
    for (int mi = 0; mi < nmems; mi++) { Mem *m = &mems[mi];
        for (uint32_t w = 0; w < m->depth; w++) {
            int64_t off = m->base + (int64_t)w * m->dw; uint64_t lo = m->wlt[w] > t0 ? m->wlt[w] : t0;
            if (t1 > lo) for (int i = 0; i < m->dw; i++) { char c = cur[off + i]; Tt[3 * (off + i) + (c == '0' ? 0 : c == '1' ? 1 : 2)] += t1 - lo; }
            mem_words++; mem_bits += (size_t)m->dw; if (m->written[w]) mem_written++;
        } }

    /* SAIF */
    out = fopen(outp, "w"); if (!out) die(3, "cannot write", outp);
    setvbuf(out, NULL, _IOFBF, 1 << 20);
    fprintf(out, "(SAIFILE\n(SAIFVERSION \"2.0\")\n(DIRECTION \"backward\")\n(DESIGN )\n(DATE \"none\")\n(VENDOR \"fst2saif\")\n(PROGRAM_NAME \"fst2saif\")\n(VERSION \"1.0\")\n(DIVIDER / )\n(TIMESCALE 1 ns)\n(DURATION %llu)\n", (unsigned long long)DUR);
    /* ancestors of the tile: nested nets-less instances, split from --scope */
    { char *sc = strdup(scope); char *comp[4096]; int nc = 0;
      for (char *p = strtok(sc, "/"); p; p = strtok(NULL, "/")) comp[nc++] = p;
      for (int i = 0; i < nc - 1; i++) { ind(i); fputs("(INSTANCE ", out); esc(comp[i]); fputc('\n', out); }
      write_node(tilenode, nc - 1);
      for (int i = nc - 2; i >= 0; i--) { ind(i); fputs(")\n", out); }
      free(sc); }
    fputs(")\n", out); fclose(out);
    if (profp) {
        FILE *pf = fopen(profp, "w"); if (!pf) die(3, "cannot write", profp);
        fputs("bucket,first_cycle,toggles\n", pf);
        for (size_t b = 0; b < nprof; b++) fprintf(pf, "%zu,%llu,%llu\n", b, (unsigned long long)(b * bucket), (unsigned long long)prof[b]);
        fclose(pf);
    }
    uint64_t ntc = 0; for (size_t b = 0; b < nbits; b++) ntc += TC[b];
    struct rusage ru; getrusage(RUSAGE_SELF, &ru);
    printf("variables=%zu\nbits=%zu\naliases=%zu\narrays_skipped=%zu\nothers_skipped=%zu\ntotal_tc=%llu\nedges=%zu\nwindow_start_time=%llu\nwindow_end_time=%llu\nperiod_time=%.6f\ntimescale_exp=%d\nperiod_ns=%.6f\nwall_s=%.2f\ncpu_s=%.2f\npeak_rss_kb=%ld\n",
           nvars, nbits, aliases, arrays_skipped, others_skipped, (unsigned long long)ntc, nedges, (unsigned long long)t0, (unsigned long long)t1,
           (double)WDEN / (double)N, (int)fstReaderGetTimescale(ctx), (double)WDEN / (double)N * pow(10.0, (double)fstReaderGetTimescale(ctx) + 9.0),
           now_s() - w0, ru.ru_utime.tv_sec + ru.ru_utime.tv_usec * 1e-6 + ru.ru_stime.tv_sec + ru.ru_stime.tv_usec * 1e-6, ru.ru_maxrss);
    if (arrays) printf("arrays_emitted=%zu\n", arrays_emitted);
    if (nmems) printf("mem_words=%zu\nmem_words_written=%zu\nmem_bits=%zu\nmem_tc=%llu\n", mem_words, mem_written, mem_bits, (unsigned long long)mem_tc);
    fstReaderClose(ctx);
    return 0;
}
