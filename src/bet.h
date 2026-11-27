/***************************************************************************
*                           STAR WARS REALITY 1.0                          *
*--------------------------------------------------------------------------*
* Star Wars Reality 1.0						           *
* copyright (c) 1997, 1998 by Sean Cooper                                  *
* -------------------------------------------------------------------------*
* Starwars and Starwars Names copyright(c) Lucasfilm Ltd.                  *
*--------------------------------------------------------------------------*
* SMAUG 1.0 (C) 1994, 1995, 1996 by Derek Snider                           *
* SMAUG code team: Thoric, Altrag, Blodkai, Narn, Haus,                    *
* Scryn, Rennard, Swordbearer, Gorog, Grishnakh and Tricops                *
* ------------------------------------------------------------------------ *
* Merc 2.1 Diku Mud improvments copyright (C) 1992, 1993 by Michael        *
* Chastain, Michael Quan, and Mitchell Tse.                                *
* Original Diku Mud copyright (C) 1990, 1991 by Sebastian Hammer,          *
* Michael Seifert, Hans Henrik St{rfeldt, Tom Madsen, and Katja Nyboe.     *
* ------------------------------------------------------------------------ *
*			Auction bid parsing routines			   *
****************************************************************************/
#ifndef BET_H
#define BET_H

#include <ctype.h>
#include <climits>
#include <string>


/*
 * The following code was written by Erwin Andreasen for the automated
 * auction command.
 *
 * Completely cleaned up by Thoric
 */


/*
  util function, converts an 'advanced' ASCII-number-string into a number.
  Used by parsebet() but could also be used by do_give or do_wimpy.

  Advanced strings can contain 'k' (or 'K') and 'm' ('M') in them, not just
  numbers. The letters multiply whatever is left of them by 1,000 and
  1,000,000 respectively. Example:

  14k = 14 * 1,000 = 14,000
  23m = 23 * 1,000,0000 = 23,000,000

  If any digits follow the 'k' or 'm', the are also added, but the number
  which they are multiplied is divided by ten, each time we get one left. This
  is best illustrated in an example :)

  14k42 = 14 * 1000 + 14 * 100 + 2 * 10 = 14420

  Of course, it only pays off to use that notation when you can skip many 0's.
  There is not much point in writing 66k666 instead of 66666, except maybe
  when you want to make sure that you get 66,666.

  More than 3 (in case of 'k') or 6 ('m') digits after 'k'/'m' are automatically
  disregarded. Example:

  14k1234 = 14,123

  If the number contains any other characters than digits, 'k' or 'm', the
  function returns 0. It also returns 0 if 'k' or 'm' appear more than
  once.
*/

inline int advatoi(const std::string& s)
{
    long long number = 0; /* number to be returned, checked against INT_MAX */
    int multiplier = 0;   /* multiplier used to get the extra digits right */
    size_t pos = 0;

    if (s.empty() || !isdigit(static_cast<unsigned char>(s[0])))
        return 0;

    /*
     * as long as the current character is a digit add to current number
     */
    while (pos < s.size() && isdigit(static_cast<unsigned char>(s[pos])))
    {
        int digit = s[pos++] - '0';
        if (number > (static_cast<long long>(INT_MAX) - digit) / 10LL)
            return 0;
        number = (number * 10LL) + digit;
    }

    if (pos < s.size())
    {
        char c = static_cast<char>(toupper(static_cast<unsigned char>(s[pos])));
        switch (c)
        {
            case 'K':
                multiplier = 1000;
                if (number > static_cast<long long>(INT_MAX) / multiplier)
                    return 0;
                number *= multiplier;
                ++pos;
                break;
            case 'M':
                multiplier = 1000000;
                if (number > static_cast<long long>(INT_MAX) / multiplier)
                    return 0;
                number *= multiplier;
                ++pos;
                break;
            default:
                return 0; /* not k nor m nor end - return 0! */
        }
    }

    /* if any digits follow k/m, add those too */
    while (pos < s.size()
        && isdigit(static_cast<unsigned char>(s[pos]))
        && multiplier > 1)
    {
        /* the further we get to right, the less the digit 'worth' */
        multiplier /= 10;
        long long add = static_cast<long long>(s[pos++] - '0') * multiplier;
        if (number > static_cast<long long>(INT_MAX) - add)
            return 0;
        number += add;
    }

    /* return 0 if any remaining character is non-digit (e.g., "14k1234x") */
    while (pos < s.size())
    {
        if (!isdigit(static_cast<unsigned char>(s[pos])))
            return 0;
        ++pos;
    }

    if (number < 0 || number > INT_MAX)
        return 0;

    /* anything left is likely extra digits (ie: 14k4443 -> 3 is extra) */
    return static_cast<int>(number);
}


/*
  Parse strict non-negative integer suffix for parsebet relative modifiers.
*/
inline bool parsebet_uint(const std::string& s, size_t start, long long& out)
{
    if (start >= s.size())
        return false;

    long long val = 0;
    for (size_t i = start; i < s.size(); ++i)
    {
        if (!isdigit(static_cast<unsigned char>(s[i])))
            return false;
        int digit = s[i] - '0';
        if (val > (static_cast<long long>(INT_MAX) - digit) / 10LL)
            return false;
        val = (val * 10LL) + digit;
    }
    out = val;
    return true;
}


/*
  This function allows the following kinds of bets to be made:

  Absolute bet
  ============

  bet 14k, bet 50m66, bet 100k

  Relative bet
  ============

  These bets are calculated relative to the current bet. The '+' symbol adds
  a certain number of percent to the current bet. The default is 25, so
  with a current bet of 1000, bet + gives 1250, bet +50 gives 1500 etc.
  Please note that the number must follow exactly after the +, without any
  spaces!

  The '*' or 'x' bet multiplies the current bet by the number specified,
  defaulting to 2. If the current bet is 1000, bet x  gives 2000, bet x10
  gives 10,000 etc.

*/
inline int parsebet(const int currentbet, const std::string& s)
{
    /* check to make sure it's not blank */
    if (s.empty())
        return 0;

    /* if first char is a digit, use advatoi */
    if (isdigit(static_cast<unsigned char>(s[0])))
        return advatoi(s);

    if (currentbet <= 0)
        return 0;

    if (s[0] == '+')      /* add percent (default 25%) */
    {
        long long pct = 25;
        if (s.size() > 1 && !parsebet_uint(s, 1, pct))
            return 0;

        long long factor = 100LL + pct;
        long long res = (static_cast<long long>(currentbet) * factor) / 100LL;
        if (res <= 0 || res > INT_MAX)
            return 0;
        return static_cast<int>(res);
    }

    if (s[0] == '*' || s[0] == 'x') /* multiply (default is by 2) */
    {
        long long mult = 2;
        if (s.size() > 1 && !parsebet_uint(s, 1, mult))
            return 0;

        if (mult <= 0)
            return 0;

        long long cur = static_cast<long long>(currentbet);
        if (cur > static_cast<long long>(INT_MAX) / mult)
            return 0;

        long long res = cur * mult;
        if (res <= 0 || res > INT_MAX)
            return 0;
        return static_cast<int>(res);
    }

    return 0;
}

#endif /* BET_H */
