/// The whole look of the app lives in this folder. Import this file (or
/// `core/widgets.dart`, which also exports the components) and nothing else.
///
///   tokens.dart      colour, spacing, type, motion: edit this to change the look
///   shape.dart       the corner language (chamfered panels, sharp controls)
///   ornaments.dart   HUD detail: readout labels, corner brackets, grid
///   smooth_scroll.dart  eased mouse-wheel scrolling (KSmoothController)
///   theme_data.dart  Flutter's ThemeData built from the tokens
///   components.dart  the K* widgets (cards, buttons, tabs, loaders, ...)
///   gallery.dart     a preview page for all of the above
library;

export 'ornaments.dart';
export 'shape.dart';
export 'smooth_scroll.dart';
export 'theme_data.dart';
export 'tokens.dart';
