Misc things
 - ~~psychopy frequently operates in floats, even for window sizes. Accept floats
   for SharedTarget and coerce to ints?~~ Done: target sizes accept integral floats;
   fractional or non-finite values raise ValueError.
 - KHR_xmp_json_ld?
 - Feels like wheel was reinvented unnecessarily, e.g. some of the custom shaders?
 - FILAMENT_OPENGL_HANDLE_ARENA_SIZE_IN_MB seems awkward for user
 