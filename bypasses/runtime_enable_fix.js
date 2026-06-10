// Runtime.Enable CDP Leak Fix
// Blocks the console.debug stack getter detection technique.
// This is the #1 detection vector for CDP-based automation in 2024-2025.
//
// Detection works by defining a getter on Error.stack and passing the Error
// to console.debug(). If Runtime.Enable is active, Chrome serializes the Error,
// triggering the getter. This script neutralizes that by wrapping console methods.

(function() {
    // Wrap console.debug to prevent Error stack getter exploitation
    const origDebug = console.debug;
    const origLog = console.log;
    const origInfo = console.info;
    const origWarn = console.warn;
    const origError = console.error;

    function safeConsole(origFn) {
        return function(...args) {
            // Clone args to prevent getter triggers during serialization
            const safeArgs = args.map(arg => {
                if (arg instanceof Error) {
                    // Return a plain string instead of the Error object
                    // This prevents the stack getter from being triggered
                    return arg.message || String(arg);
                }
                return arg;
            });
            return origFn.apply(console, safeArgs);
        };
    }

    // Only wrap debug (the primary detection vector)
    // Wrapping all console methods would be suspicious
    console.debug = safeConsole(origDebug);

    // Make it look native
    console.debug.toString = () => 'function debug() { [native code] }';
})();
