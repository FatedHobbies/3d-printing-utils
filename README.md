Introduction
============

This repo contains a set of utilities for using Bambu Labs 3d printers from networked Windows computers.

The goal is to be able to leverage the computational power of a computer on the network for slicing, while freeing up that computer from the other associated 3d printing tasks.

Architecture
============

Currently, there are two computers involved. Let's give them names:

1. Slicer
2. Manager

Slicer _only_ slices. It should never be involved with managing the 3D printers themselves. It's a powerful computer reserved for more intense computational tasks (including slicing).

Manager is where the user sits. This is the computer actually connected to the 3d printers and selecting the appropriate settings for the printers.

The user will select the file, printer, material, and print profile on Manager. Once happy with the selected settings, the user requires that Slicer does the actual slicing of the file. The result of slicing is then sent to Manager to deliver to the pre-selected printer (remember, the user selected this printer before slicing).