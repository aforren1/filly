function setLogLevel(level)
% SETLOGLEVEL  Minimum Filament log severity for all renderers in this process:
%   'verbose', 'debug', 'info', 'warning', 'error', or 'off'.
filly_mex('setLogLevel', filly.internal.text(level));
end
