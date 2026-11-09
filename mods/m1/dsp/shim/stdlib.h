/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Freestanding stand-in for <stdlib.h> (S6c): DaisySP's noise calls rand(). Ours (../drums.cpp) is a xorshift32 on
 * the state of the drum cell being rendered, so every voice has its own sequence and the ELF has no RAM data. */
#pragma once
#define RAND_MAX 0x7FFFFFFF
extern "C" int rand(void);
