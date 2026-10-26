function text = error_report(err)
% ERROR_REPORT  The message and call stack of a caught error, as text.
%   Octave has no getReport, and its caught errors are not MException objects.
if exist('OCTAVE_VERSION', 'builtin') == 0
    text = getReport(err, 'extended', 'hyperlinks', 'off');
    return
end
text = err.message;
if ~isempty(err.identifier)
    text = sprintf('%s (%s)', text, err.identifier);
end
for i = 1:numel(err.stack)
    frame = err.stack(i);
    text = sprintf('%s\n    at %s, line %d (%s)', text, frame.name, frame.line, frame.file);
end
end
