# Bellwright Dev
Just some tools and resources around doing fan development around Bellwright

## Icons
Right now its only icons I extracted for another project and thought it would be nice to make these assets public.

You'll find the icons that I extracted from Bellwright Modkit in **bellwright-icons.zip**. Here is also a python script `uasset_source.py` that I created with claude. I enables you to extract the icons from uasset-files that are found in Modkit subdirectories so that you don't need to open modkit. 

Example:
`python uasset_source.py C:\Program Files\Epic Games\BellwrightModkit\Game\Content\Mist\UI\Icons out`

More documentation in the script itself. You will need to install python >= 3.14 and run `pip install pillow numpy`.