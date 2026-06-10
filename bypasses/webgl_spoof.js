// WebGL Renderer Spoofing
// Overrides WEBGL_debug_renderer_info to report a real consumer GPU instead of
// SwiftShader/Mesa/LLVMpipe (the headless-server tell).
//
// The renderer string MUST match the host OS. The system Chrome here runs on Linux,
// so we report the Linux ANGLE-over-OpenGL form ("ANGLE (NVIDIA Corporation, ...
// OpenGL ...)"). A Windows "Direct3D11" string on a Linux UA is an instant
// inconsistency flag (was: 'ANGLE (NVIDIA, ... Direct3D11 ..., D3D11)').

(function() {
    const VENDOR = 'Google Inc. (NVIDIA Corporation)';
    const RENDERER = 'ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 3060/PCIe/SSE2, OpenGL 4.6.0)';

    // Extension constants
    const UNMASKED_VENDOR_WEBGL = 0x9245;
    const UNMASKED_RENDERER_WEBGL = 0x9246;

    function patchGetParameter(proto) {
        const orig = proto.getParameter;
        // Proxy apply-trap keeps toString/name/length reporting the native original,
        // instead of the old `fn.toString = () => '...'` shim which a real detector
        // defeats via Function.prototype.toString.call() / hasOwnProperty('toString').
        proto.getParameter = __usMask(orig, function(target, thisArg, args) {
            const param = args[0];
            if (param === UNMASKED_VENDOR_WEBGL) return VENDOR;
            if (param === UNMASKED_RENDERER_WEBGL) return RENDERER;
            return Reflect.apply(target, thisArg, args);
        });
    }

    // Patch getExtension to return the debug info extension
    function patchGetExtension(proto) {
        const orig = proto.getExtension;
        proto.getExtension = __usMask(orig, function(target, thisArg, args) {
            if (args[0] === 'WEBGL_debug_renderer_info') {
                return {
                    UNMASKED_VENDOR_WEBGL: UNMASKED_VENDOR_WEBGL,
                    UNMASKED_RENDERER_WEBGL: UNMASKED_RENDERER_WEBGL
                };
            }
            return Reflect.apply(target, thisArg, args);
        });
    }

    if (typeof WebGLRenderingContext !== 'undefined') {
        patchGetParameter(WebGLRenderingContext.prototype);
        patchGetExtension(WebGLRenderingContext.prototype);
    }
    if (typeof WebGL2RenderingContext !== 'undefined') {
        patchGetParameter(WebGL2RenderingContext.prototype);
        patchGetExtension(WebGL2RenderingContext.prototype);
    }
})();
