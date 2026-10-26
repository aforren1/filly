function value = text(value)
% TEXT  A char vector from a char vector or a string scalar. The gateway reads char only.
if ~ischar(value)
    value = char(value);
end
end
