function ptbActivateAny(windows)
% PTBACTIVATEANY  Make the context of the first open window in the list current, if any is open.
if ~filly.internal.ptbAvailable()
    return
end
open = Screen('Windows');
for window = unique(windows(:))'
    if any(open == window) && Screen('WindowKind', window) == 1
        filly.internal.ptbActivate(window);
        return
    end
end
end
