cursedlight
===========

A urwid-based interface for controlling light shows. (It was originally based on curses)

This was used as the interface for controlling ~15 50-pixel light strips running bespeckle ( http://github.com/zbanks/bespeckle ). 
It was quickly cobbled together over the course of a few (sleep-deprived) days, and managed to work "well-enough".

After using it for 5 hours straight, I've figured out some things that need to be improved:
 * There needs to be a way to set a solid color background (or fade to a solid color)
 * "Pulses" are great, but it'd be better if you could tweak the timing/size more
 * Possibly an auto pulse generator: a lot of work was just stacking a bunch of pulse effects with different timings
 * Strobes work. The current setup is pretty good, but maybe default to every beat instead of every downbeat.
 * Fade to black was wonderful. 
 * Being able to manually set the strip to a solid color was useful for special songs (ex. Danger! High Voltage!)
 * The soft rainbow effect was a bit hard to use well, but was reasonable filler. 
 * The strobing rainbow effect (Cycling between 6 colors) was great for strong chorus dancing
 * Strobes can only be used if you're synced to the song's beat. Non-strobing effects are critical for older songs.
 * Separate keyboards worked well, until you wanted to simultaneously trigger events that required modifier keys. 
 * Modifier key for "all"? Or "repeat the other event here too"? 
 * How can we figure out which buttons you can hold down at the same time on a keyboard?
 * Toggling effects made sense 40% of the time. Other times effects would just "replace" old ones. Ex. fade to black, stack effects, then fade to black *again*. Ex. Solid red, solid green, solid red, *again*. 

Overall, bespeckle only needs a bit more work; the interface needs a lot more. The paradigms work well enough. Color stickers should be applied liberally to your keyboard for your own sanity. 
