// navigator.webdriver -> false, with a getter that reads as native.
//
// NOTE: the previous version used `const x = function get webdriver() {...}`, which is
// a SYNTAX ERROR (a function name must be a single identifier). Because all bypass
// scripts are concatenated into one init script, that error made V8 reject the WHOLE
// script — silently disabling every bypass. This version is valid and undetectable.
//
// The object-literal getter shorthand produces a function whose .name is natively
// "get webdriver" (exactly what real Chrome reports); __usMask wraps it in a Proxy so
// Function.prototype.toString.call() returns "[native code]" with no own toString leak.
(function() {
    const realGetter = Object.getOwnPropertyDescriptor(
        { get webdriver() { return false; } },
        'webdriver'
    ).get;
    const maskedGetter = __usMask(realGetter, function(target, thisArg, args) {
        return false;
    });
    Object.defineProperty(Navigator.prototype, 'webdriver', {
        get: maskedGetter,
        set: undefined,
        enumerable: true,
        configurable: true
    });
})();