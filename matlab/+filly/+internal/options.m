function values = options(defaults, args)
% OPTIONS  Name-value arguments over a struct of defaults. Names match without case.
%   inputParser is not used: it costs tens of microseconds per call, and some methods run
%   every frame.
values = defaults;
if isempty(args)
    return
end
if mod(numel(args), 2) ~= 0
    error('filly:invalidArgument', 'Options must be name-value pairs');
end
names = fieldnames(defaults);
for i = 1:2:numel(args)
    name = args{i};
    if ~ischar(name)
        name = char(name);
    end
    match = strcmpi(names, name);
    if ~any(match)
        error('filly:invalidArgument', 'Unknown option ''%s''. Options are: %s', name, strjoin(names', ', '));
    end
    values.(names{match}) = args{i + 1};
end
end
