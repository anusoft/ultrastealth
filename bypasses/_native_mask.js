// Robust native-function masking helper (loaded first, shared by later bypasses).
//
// Replacing a native prototype method with a plain JS function is detectable:
// `Function.prototype.toString.call(fn)` returns the JS source (not
// "[native code]"), the replacement's `.name` is "" and `.length` is wrong, and
// it gains an own `toString` property that real native functions never have.
// CreepJS / deviceandbrowserinfo / fingerprint-scan all probe exactly these.
//
// Wrapping the original native method in a Proxy with only an `apply` trap fixes
// all of that at once: toString/name/length/own-property descriptors transparently
// forward to the untouched native target, so the spoof reads as native.
//
// `__usMask` is a top-level `const` in this classic init script — it is visible to
// the IIFEs concatenated after it, but is NOT a property of window/globalThis, so
// page JavaScript cannot see it.
const __usMask = (orig, applyTrap) =>
  new Proxy(orig, { apply: applyTrap });
