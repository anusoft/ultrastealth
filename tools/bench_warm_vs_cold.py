"""Measure cold-start vs warm-attach latency for a trivial navigate + title.

Cold: launch a fresh UltrastealthFetcher each run (boots Chrome every time).
Warm: attach to the warm daemon (first call starts it once, the rest reuse it).

Usage:
    python tools/bench_warm_vs_cold.py --url https://example.com --runs 3

Requires a real browser (run outside CI, under a display on Linux). Prints a
table and the average warm-vs-cold speedup.
"""
import argparse
import asyncio
import time


async def cold_once(url):
    from ultrastealth import UltrastealthFetcher
    t0 = time.time()
    async with UltrastealthFetcher(headless=False) as us:
        await us.fetch_and_evaluate(url=url, js_expression="() => document.title", wait_secs=0.5)
    return time.time() - t0


async def warm_once(url):
    from ultrastealth import connect
    us = connect()
    t0 = time.time()
    await us.call("navigate", url=url, wait_secs=0.5)
    await us.call("get", kind="title")
    return time.time() - t0


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://example.com")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    cold = [await cold_once(args.url) for _ in range(args.runs)]
    warm = [await warm_once(args.url) for _ in range(args.runs)]  # 1st warms, rest reuse

    print(f"{'run':>4} {'cold (s)':>10} {'warm (s)':>10}")
    for i, (c, w) in enumerate(zip(cold, warm)):
        print(f"{i:>4} {c:>10.2f} {w:>10.2f}")
    c_avg, w_avg = sum(cold) / len(cold), sum(warm) / len(warm)
    print(f"{'avg':>4} {c_avg:>10.2f} {w_avg:>10.2f}")
    print(f"warm reuse (runs 1+): {sum(warm[1:]) / max(len(warm) - 1, 1):.2f}s")
    print(f"speedup (avg): {c_avg / max(w_avg, 1e-6):.1f}x")


if __name__ == "__main__":
    asyncio.run(main())
