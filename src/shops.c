/***************************************************************************
*                           STAR WARS REALITY 1.0                          *
*--------------------------------------------------------------------------*
* Star Wars Reality Code Additions and changes from the Smaug Code         *
* copyright (c) 1997 by Sean Cooper                                        *
* -------------------------------------------------------------------------*
* Starwars and Starwars Names copyright(c) Lucas Film Ltd.                 *
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
*			 Shop and repair shop module			   *
****************************************************************************/

#include <sys/types.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <ctype.h>
#include <stdarg.h>
#include <cerrno>
#include <climits>
#include <cstdlib>
#include <sys/stat.h>
#include "mud.h"

static constexpr int VENDOR_FEE_DIVISOR = 20; // Five percent of the vendor earnings.

#if defined(KEY)
#undef KEY
#endif

#define KEY( literal, field, value )					\
				if ( !strcmp( word, literal ) )	\
				{					\
				    field  = value;			\
				    fMatch = TRUE;			\
				    break;				\
				}

std::string strip_aoran( const std::string &input );				

/*
 * Local functions
 */

#define	CD	CHAR_DATA
CD *	find_keeper	args( ( CHAR_DATA *ch ) );
CD *	find_keeper_q	args( ( CHAR_DATA *ch, bool message ) );
CD *	find_fixer	args( ( CHAR_DATA *ch ) );
int	get_cost	args( ( CHAR_DATA *ch, CHAR_DATA *keeper,
				OBJ_DATA *obj, bool fBuy ) );
int 	get_repaircost  args( ( CHAR_DATA *keeper, OBJ_DATA *obj ) );
#undef CD

const flag_name shop_types[] = {
    { SHOP_BUY, "buy" },
    { SHOP_FIX, "fix" },
    { SHOP_RECHARGE, "recharge" },
    { SHOP_MAX, "" },
    { (size_t)-1, nullptr } // terminator
};

int get_shoptype( const std::string& type )
{
	const flag_name* x;
	x = find_flag(shop_types, type);
	if (!x)
		return -1;
	return x->bit;
}

/*
 * Shopping commands.
 */
CHAR_DATA *find_keeper( CHAR_DATA *ch )
{
  return find_keeper_q( ch, TRUE );
}

static const SHOP_DATA *buying_shop(const CHAR_DATA *keeper)
{
    if ( !keeper->owner )
        return keeper->pIndexData ? keeper->pIndexData->pShop : nullptr;

    static const SHOP_DATA player_vendor_shop = [] {
        SHOP_DATA shop{};
        BV_SET_BIT(shop.shop_type, SHOP_BUY);
        shop.open_hour = 0;
        shop.close_hour = 23;
        return shop;
    }();
    return &player_vendor_shop;
}

CHAR_DATA *find_keeper_q( CHAR_DATA *ch, bool message )
{
    if ( !ch )
        return NULL;

    CHAR_DATA *keeper;
    const SHOP_DATA *pShop = nullptr;
    for ( keeper = ch->in_room ? ch->in_room->first_person : NULL;
	  keeper;
	  keeper = keeper->next_in_room )
	if ( IS_NPC(keeper) && keeper->pIndexData
	&&   (pShop = buying_shop(keeper)) != nullptr
	&&   BV_IS_SET(pShop->shop_type, SHOP_BUY) )
	    break;

    if ( !keeper )

    {
      if ( message )
	send_to_char( "You can't do that here.\n", ch );
      return NULL;
    }


    /*
     * Shop hours.
     */
    if ( pShop->open_hour > pShop->close_hour )
    {
      if( time_info.hour < pShop->open_hour && time_info.hour > pShop->close_hour )
      {
	do_say( keeper, "Sorry, come back later." );
	return NULL;
      }
    }
    else
      if( time_info.hour < pShop->open_hour || time_info.hour > pShop->close_hour )
      {
        if( time_info.hour < pShop->open_hour )
        {
	  do_say( keeper, "Sorry, come back later." );
	  return NULL;
        }
        if ( time_info.hour > pShop->close_hour )
        {
	  do_say( keeper, "Sorry, come back tomorrow." );
	  return NULL;
        }
      }
    if ( !knows_language( keeper, ch->speaking, ch ) )
    {
	do_say( keeper, "I can't understand you." );
	return NULL;
    }

    return keeper;
}

/*
 * repair commands.
 */
CHAR_DATA *find_fixer( CHAR_DATA *ch )
{
    if ( !ch )
        return NULL;

    CHAR_DATA *keeper;
    SHOP_DATA *rShop = NULL;
    for ( keeper = ch->in_room ? ch->in_room->first_person : NULL; keeper; keeper = keeper->next_in_room )
		if ( IS_NPC(keeper) && keeper->pIndexData
		&&   (rShop = keeper->pIndexData->pShop) != NULL
		&&   ( BV_IS_SET(rShop->shop_type, SHOP_FIX) || BV_IS_SET(rShop->shop_type, SHOP_RECHARGE) ) )
		    break;

    if ( !keeper )
    {
	send_to_char( "You can't do that here.\n", ch );
	return NULL;
    }

 
    /*
     * Shop hours.
     */
    const int open_hour = rShop->separate_repair ? rShop->repair_open_hour : rShop->open_hour;
    const int close_hour = rShop->separate_repair ? rShop->repair_close_hour : rShop->close_hour;
    if ( open_hour > close_hour )
    {
	if ( time_info.hour < open_hour && time_info.hour > close_hour )
	{
	    do_say( keeper, "Sorry, come back later." );
	    return NULL;
	}
    }
    else if ( time_info.hour < open_hour || time_info.hour > close_hour )
    {
	if ( time_info.hour < open_hour )
	{
	    do_say( keeper, "Sorry, come back later." );
	    return NULL;
	}
	if ( time_info.hour > close_hour )
	{
	    do_say( keeper, "Sorry, come back tomorrow." );
	    return NULL;
	}
    }

    
    if ( !knows_language( keeper, ch->speaking, ch ) )
    {
	do_say( keeper, "I can't understand you." );
	return NULL;
    }

    return keeper;
}

int get_cost_quit( CHAR_DATA *ch )
{
	
  long long cost = 1000;
  long long golddem = 100000;
  long long gold;
  
  if( !ch )
    return 0;
  
  if( ch->top_level <= 6 )
    return 0;
  
  gold = (long long)ch->gold + ((!IS_NPC(ch) && ch->pcdata) ? (long long)ch->pcdata->bank : 0LL) + 1LL;
  
  if( gold < 5000 )
    return 0;
  cost *= gold/golddem;
  if ( cost < 0 )
    cost = 0;
  if ( cost > INT_MAX )
    cost = INT_MAX;

  return (int) cost;
}

static int scale_shop_cost(int cost, long long condition, long long maximum)
{
    if ( cost <= 0 || condition <= 0 || maximum <= 0 )
        return 0;
    return static_cast<int>(UMIN(INT_MAX, cost * condition / maximum));
}

int get_cost( CHAR_DATA *ch, CHAR_DATA *keeper, OBJ_DATA *obj, bool fBuy )
{
    if ( !ch || !obj || !obj->pIndexData || !keeper || !keeper->pIndexData )
        return 0;
    const SHOP_DATA *shop = buying_shop(keeper);
    if ( !shop )
        return 0;
    if ( keeper->owner )
        return fBuy ? UMAX(0, obj->cost) : 0;
    if ( !fBuy && !BV_IS_SET(shop->buy_type, obj->item_type) )
        return 0;

    if( obj->item_type == ITEM_FIGHTERCOMP || obj->item_type == ITEM_MIDCOMP || obj->item_type == ITEM_CAPITALCOMP )
    {
      int costunit;
      
      switch ( obj->value[1] )
      {
      	case MOD_HYPERSPEED: costunit = 1000; break;
      	case MOD_REALSPEED: costunit = 500; break;
      	case MOD_LASER: costunit = 10000; break;
      	case MOD_ION: costunit = 12500; break;
      	case MOD_MAXSHIELD: costunit = 1000; break;
      	case MOD_ENERGY: costunit = 10; break;
      	case MOD_LAUNCHER: costunit = 20000; break;
      	case MOD_TRACTORBEAM: costunit = 2500; break;
      	case MOD_COMM: costunit = 75; break;
      	case MOD_SENSOR: costunit = 125; break;
      	case MOD_ASTRO_ARRAY: costunit = 125; break;
      	case MOD_DEFENSELAUNCHER: costunit = 10000; break;
      	case MOD_MANUEVER: costunit = 1000; break;
      	case MOD_HULL: costunit = 200; break;
      	case MOD_MISSILE: costunit = 1000; break;
      	case MOD_TORPEDO: costunit = 1600; break;
      	case MOD_ROCKET: costunit = 2000; break;
      	case MOD_CHAFF: costunit = 400; break;
      	case MOD_GRAVITY_PROJ: costunit = 1000; break;
      	case MOD_TURRET: costunit = 100000; break;
      	case MOD_CARGO: costunit = 1; break;
      	default: costunit = 100000; break;
      }
      
      if( obj->item_type == ITEM_CAPITALCOMP )
      {
      	if ( obj->value[1] == MOD_MAXSHIELD )
      	  costunit *= 5;
        costunit *= 2;
      }
        
      const long long rating = obj->value[3] ? obj->value[3] : obj->value[2];
      return static_cast<int>(URANGE(0LL, rating * costunit, static_cast<long long>(INT_MAX)));
    }

    const long long threshold = 1000LL * ch->top_level - ch->gold;
    const long bank = (!IS_NPC(ch) && ch->pcdata) ? ch->pcdata->bank : 0;
    const bool rich_customer = bank > threshold;
    long long cost;
    if ( fBuy )
    {
        const int modifier = 13 - get_curr_cha(ch) + (rich_customer ? 15 : 0)
                             + (URANGE(5, ch->top_level, LEVEL_AVATAR) - 20) / 2;
        cost = 1LL * obj->cost * UMAX(shop->profit_sell + 1, shop->profit_buy + modifier) / 100;
    }
    else
    {
        const int modifier = get_curr_cha(ch) - 13 - (rich_customer ? 15 : 0);
        cost = 1LL * obj->cost * UMIN(shop->profit_buy - 1, shop->profit_sell + modifier) / 100;
        if ( obj->item_type != ITEM_SPICE )
            for ( const OBJ_DATA *stock = keeper->first_carrying; stock; stock = stock->next_content )
                if ( obj->pIndexData == stock->pIndexData )
                {
                    const int divisor = stock->count + 1;
                    if ( divisor <= 0 )
                        return 0;
                    cost /= divisor;
                    break;
                }
        cost = UMIN(cost, 2500);
    }

    if ( cost <= 0 )
        return 0;
    if ( obj->item_type != ITEM_CARGO && obj->item_type != ITEM_SPICE )
        cost = UMAX(1, (cost * 100) / URANGE(50, obj->pIndexData->count, 500));

    const int price = static_cast<int>(UMIN(INT_MAX, cost));
    switch ( obj->item_type )
    {
    case ITEM_ARMOR:
        return scale_shop_cost(price, obj->value[0] + 1LL, obj->value[1] + 1LL);
    case ITEM_WEAPON:
        return scale_shop_cost(
            scale_shop_cost(price, obj->value[0] + 1LL, INIT_WEAPON_CONDITION + 1LL),
            obj->value[4] + 1LL, obj->value[5] + 1LL);
    case ITEM_DEVICE:
        return scale_shop_cost(price, obj->value[2] + 1LL, obj->value[1] + 1LL);
    default:
        return price;
    }
}

int get_repaircost( CHAR_DATA *keeper, OBJ_DATA *obj )
{
    SHOP_DATA *pShop;
    int cost;
    bool found;

    if ( !obj || !keeper || !keeper->pIndexData || ( pShop = keeper->pIndexData->pShop ) == NULL )
	return -1;

    cost = 0;
    found = FALSE;
    const FLAG_SET &repair_type = pShop->separate_repair ? pShop->repair_type : pShop->buy_type;
    if ( BV_IS_SET(repair_type, obj->item_type) )
    {
	bool eligible = false;
	if ( ( obj->item_type == ITEM_ARMOR || obj->item_type == ITEM_WEAPON )
	&&   BV_IS_SET(pShop->shop_type, SHOP_FIX) )
	    eligible = true;
	else if ( obj->item_type == ITEM_DEVICE
	     &&   BV_IS_SET(pShop->shop_type, SHOP_RECHARGE) )
	    eligible = true;

	if ( eligible )
	{
	    long long calc = (long long)obj->cost * pShop->profit_fix / 100;
	    cost = static_cast<int>(std::clamp(calc, 1LL, static_cast<long long>(INT_MAX)));
	    found = TRUE;
	}
    }

    if ( !found )
      cost = -1;

    if ( found && cost > 0 )
    {
      long long delta = 0;
      switch (obj->item_type)
      {
	case ITEM_ARMOR:
	  if (obj->value[0] >= obj->value[1])
	    cost = -2;
	  else
	    delta = cost * (static_cast<long long>(obj->value[1]) - obj->value[0]);
	  break;
	case ITEM_WEAPON:
	  if (obj->value[0] >= INIT_WEAPON_CONDITION)
	    cost = -2;
	  else
	    delta = cost * (static_cast<long long>(INIT_WEAPON_CONDITION) - obj->value[0]);
	  break;
	case ITEM_DEVICE:
	  if (obj->value[2] >= obj->value[1])
	    cost = -2;
	  else
	    delta = cost * (static_cast<long long>(obj->value[1]) - obj->value[2]);
	  break;
      }
      if ( cost > 0 && delta > 0 )
      {
	  if ( delta > INT_MAX )
	      delta = INT_MAX;
	  cost = (int)delta;
      }
    }

    return cost;
}



void do_buy( CHAR_DATA *ch, char *argument )
{
    std::string arg;
	std::string argstr = argument;
    int maxgold;

    if ( !ch || !ch->in_room )
        return;

    argstr = one_argument( argstr, arg );

    if ( arg.empty() )
    {
	send_to_char( "Buy what?\n", ch );
	return;
    }

    if ( BV_IS_SET(ch->in_room->room_flags, ROOM_PET_SHOP) )
    {
	std::string buf;
	CHAR_DATA *pet;
	ROOM_INDEX_DATA *pRoomIndexNext;
	ROOM_INDEX_DATA *in_room;

	if ( IS_NPC(ch) )
	    return;

	pRoomIndexNext = get_room_index( ch->in_room->vnum + 1 );
	if ( !pRoomIndexNext )
	{
	    bug( "Do_buy: bad pet shop at vnum %d.", ch->in_room->vnum );
	    send_to_char( "Sorry, you can't buy that here.\n", ch );
	    return;
	}

	in_room     = ch->in_room;
	ch->in_room = pRoomIndexNext;
	pet         = get_char_room( ch, arg );
	ch->in_room = in_room;

	if ( pet == NULL || !IS_NPC( pet ) || !BV_IS_SET(pet->act, ACT_PET) )
	{
	    send_to_char( "Sorry, you can't buy that here.\n", ch );
	    return;
	}

	if ( BV_IS_SET(ch->act, PLR_BOUGHT_PET) )
	{
	    send_to_char( "You already bought one pet this level.\n", ch );
	    return;
	}

	const long long pet_price = 10LL * pet->top_level * pet->top_level;
	if ( ch->gold < pet_price )
	{
	    send_to_char( "You can't afford it.\n", ch );
	    return;
	}

	if ( ch->top_level < pet->top_level )
	{
	    send_to_char( "You're not ready for this pet.\n", ch );
	    return;
	}

	if (!ch->game)
	{
    	send_to_char( "You must be in a game to buy a pet.\n", ch );
	    return;
	}
	
	pet		= create_mobile( ch->game, pet->pIndexData );
        if ( !pet )
            return;
        ch->gold -= static_cast<int>(pet_price);
        boost_economy(ch->in_room->area, static_cast<int>(pet_price));
	BV_SET_BIT(ch->act, PLR_BOUGHT_PET);
	BV_SET_BIT(pet->act, ACT_PET);
	BV_SET_BIT(pet->affected_by, AFF_CHARM);

	argstr = one_argument( argstr, arg );
	if ( !arg.empty() )
	{
	    buf = str_printf( "%s %s", pet->name, arg );
	    STRFREE( pet->name );
	    pet->name = STRALLOC( buf );
	}

	buf = str_printf( "%sA neck tag says 'I belong to %s'.\n",
	    pet->description, ch->name );
	STRFREE( pet->description );
	pet->description = STRALLOC( buf );

	if( ch->pcdata )
	  ch->pcdata->pet = pet;

	char_to_room( pet, ch->in_room );
	add_follower( pet, ch );
	send_to_char( "Enjoy your pet.\n", ch );
    	act( AT_ACTION, "$n bought $N as a pet.", ch, NULL, pet, TO_ROOM );
	return;
    }
    else
    {
	CHAR_DATA *keeper;
	OBJ_DATA *obj;
	int cost;
	int noi = 1;		/* Number of items */
	sh_int mnoi = 20;	/* Max number of items to be bought at once */

	if ( ( keeper = find_keeper( ch ) ) == NULL )
	    return;

	if ( keeper == NULL )
	  return;

	maxgold = keeper->top_level * 10;

	if ( is_number( arg ) )
	{
            errno = 0;
            const long quantity = strtol(arg.c_str(), nullptr, 10);
            if ( errno == ERANGE || quantity < 1 || quantity > mnoi )
	    {
		act( AT_TELL, "$n tells you 'I don't sell that many items at"
		  " once.'", keeper, NULL, ch, TO_VICT );
		ch->reply = keeper;
		return;
	    }
	    noi = static_cast<int>(quantity);
	    argstr = one_argument( argstr, arg );
	}

	obj  = get_obj_carry( keeper, arg );
	
	if ( !obj && !arg.empty() && arg[0] == '#' )
        {     
              int onum, oref;
              bool ofound = FALSE;
              
              onum =0;
              char *end;
              errno = 0;
              const long ref = strtol(arg.c_str() + 1, &end, 10);
              if ( errno == ERANGE || *end || ref < 1 || ref > INT_MAX )
              {
                  send_to_char("Buy what?\n", ch);
                  return;
              }
              oref = static_cast<int>(ref);
              for ( obj = keeper->last_carrying; obj; obj = obj->prev_content )
	      { 
	        if ( obj->wear_loc == WEAR_NONE
	        &&   can_see_obj( ch, obj ) )
	            onum++;
                if ( onum == oref ) 
                {
                    ofound = TRUE;
                    break;
                }
                else if ( onum > oref )
                   break;
	      }
	      if (!ofound)
	         obj = NULL;
        }

        if ( !obj )
        {
	  send_to_char( "Buy what?\n", ch );
	  return;
        }
          
	
        const bool unlimited = IS_OBJ_STAT(obj, ITEM_INVENTORY) && !keeper->owner;
        const long long total = 1LL * get_cost(ch, keeper, obj, TRUE) * noi;
        if ( total > INT_MAX || (keeper->owner && total + keeper->gold > INT_MAX) )
        {
            send_to_char("That transaction is too large.\n", ch);
            return;
        }
        cost = static_cast<int>(total);
	
	
	if ( cost <= 0 || !can_see_obj( ch, obj ) )
	{
	    act( AT_TELL, "$n tells you 'I don't sell that -- try 'list'.'",
		keeper, NULL, ch, TO_VICT );
	    ch->reply = keeper;
	    return;
	}

	if ( !unlimited && noi > 1 )
	{
	    interpret( keeper, "laugh" );
	    act( AT_TELL, "$n tells you 'I don't have enough of those in stock"
	     " to sell more than one at a time.'", keeper, NULL, ch, TO_VICT );
	    ch->reply = keeper;
	    return;
	}

        AREA_DATA *area = ch->in_room->area;
        int cargo_supply = 0;
        if ( obj->item_type == ITEM_CARGO )
        {
            const int type = obj->value[0];
            if ( !area || type < 0 || type >= static_cast<int>(sizeof(area->supply) / sizeof(area->supply[0]))
                 || obj->value[1] <= 0 )
            {
                send_to_char("That cargo is unavailable.\n", ch);
                return;
            }
            const long long after_sale = area->supply[type] - 1LL * obj->value[1] * noi;
            const long long production = 36LL * (1LL * area->production[type] - area->depletion[type]);
            if ( after_sale < 0 || after_sale + production < 3LL * area->supply[type] / 4 )
            {
                act(AT_TELL, "$n tells you 'Our local supply is running low. Come back later.'",
                    keeper, NULL, ch, TO_VICT);
                ch->reply = keeper;
                return;
            }
            cargo_supply = static_cast<int>(after_sale);
        }

	if ( ch->gold < cost )
	{
	    act( AT_TELL, "$n tells you 'You can't afford to buy $p.'",
		keeper, obj, ch, TO_VICT );
	    ch->reply = keeper;
	    return;
	}

	if ( BV_IS_SET(obj->objflags, ITEM_PROTOTYPE) 
             && get_trust( ch ) < LEVEL_IMMORTAL )
	{
	    act( AT_TELL, "$n tells you 'This is a only a prototype!  I can't sell you that...'", 
		keeper, NULL, ch, TO_VICT );
      	    ch->reply = keeper;
	    return;
	}

        OBJ_INDEX_DATA *bag_index = nullptr;
        if ( noi > 1 && !(bag_index = get_obj_index(OBJ_VNUM_SHOPPING_BAG)) )
        {
            send_to_char("The shopkeeper has no bags for a bulk purchase.\n", ch);
            return;
        }

        const int stock_count = UMAX(1, obj->count);
        if ( ch->carry_number + 1LL * noi * (get_obj_number(obj) / stock_count)
             + (bag_index ? 1 : 0) > can_carry_n(ch) )
        {
            send_to_char("You can't carry that many items.\n", ch);
            return;
        }
        if ( ch->carry_weight + 1LL * noi * (get_obj_weight(obj) / stock_count)
             + (bag_index ? bag_index->weight : 0) > can_carry_w(ch) )
        {
            send_to_char("You can't carry that much weight.\n", ch);
            return;
        }

        OBJ_DATA *purchased = unlimited ? create_object(obj->pIndexData, obj->level) : obj;
        if ( !purchased )
            return;
        OBJ_DATA *bag = bag_index ? create_object(bag_index, 1) : nullptr;
        if ( bag_index && !bag )
        {
            extract_obj(purchased);
            return;
        }
        if ( !unlimited )
            separate_obj(purchased);

	if ( noi == 1 )
	{
//	    if ( !IS_OBJ_STAT( obj, ITEM_INVENTORY ) || ( keeper->home != NULL ) )  
//            separate_obj( obj );
	    act( AT_ACTION, "$n buys $p.", ch, obj, NULL, TO_ROOM );
    	    act( AT_ACTION, "You buy $p.", ch, obj, NULL, TO_CHAR );
	}
        else
	{
		std::string desc = strip_aoran( obj->short_descr );
		arg = str_printf( "$n buys %d %s%s.", noi,
			desc.c_str(), ( !desc.empty() && desc.back() == 's' ? "" : "s" ) );
	    act( AT_ACTION, arg.c_str(), ch, obj, NULL, TO_ROOM );
	    arg = str_printf( "You buy %d %s%s.", noi,
		desc.c_str(), ( !desc.empty() && desc.back() == 's' ? "" : "s" ) );
	    act( AT_ACTION, arg.c_str(), ch, obj, NULL, TO_CHAR );
	    act( AT_ACTION, "$N puts them into a bag and hands it to you.",
		ch, NULL, keeper, TO_CHAR );
	}

        ch->gold -= cost;
        const long long keeper_gold = 1LL * keeper->gold + cost;
        if ( !keeper->owner && area && keeper_gold > maxgold )
        {
            boost_economy(area, static_cast<int>(UMIN(INT_MAX, keeper_gold - maxgold / 2)));
            keeper->gold = maxgold / 2;
            act(AT_ACTION, "$n puts some credits into a large safe.", keeper, NULL, NULL, TO_ROOM);
        }
        else
            keeper->gold = static_cast<int>(UMIN(INT_MAX, keeper_gold));

        if ( obj->item_type == ITEM_CARGO )
            area->supply[obj->value[0]] = cargo_supply;

        if ( !unlimited )
            obj_from_char(purchased);
        if ( keeper->owner )
            purchased->cost = 0;

        if ( bag )
        {
            bag->value[0] = static_cast<int>(UMIN(INT_MAX, bag->weight + 1LL * purchased->weight * noi));
            purchased->count = noi;
            purchased->pIndexData->count += noi - 1;
            numobjsloaded += noi - 1;
            obj_to_obj(purchased, bag);
            obj_to_char(bag, ch);
        }
        else
            obj_to_char(purchased, ch);

        if ( keeper->owner )
            save_vendor(keeper);

	return;
    }
}


void do_list( CHAR_DATA *ch, char *argument )
{
    if ( BV_IS_SET(ch->in_room->room_flags, ROOM_PET_SHOP) )
    {
	ROOM_INDEX_DATA *pRoomIndexNext;
	CHAR_DATA *pet;
	bool found;

	pRoomIndexNext = get_room_index( ch->in_room->vnum + 1 );
	if ( !pRoomIndexNext )
	{
	    bug( "Do_list: bad pet shop at vnum %d.", ch->in_room->vnum );
	    send_to_char( "You can't do that here.\n", ch );
	    return;
	}

	found = FALSE;
	for ( pet = pRoomIndexNext->first_person; pet; pet = pet->next_in_room )
	{
	    if ( BV_IS_SET(pet->act, ACT_PET) && IS_NPC(pet) )
	    {
		if ( !found )
		{
		    found = TRUE;
		    send_to_char( "Pets for sale:\n", ch );
		}
		ch_printf( ch, "[%2d] %8d - %s\n",
			pet->top_level,
			10 * pet->top_level * pet->top_level,
			pet->short_descr );
	    }
	}
	if ( !found )
	    send_to_char( "Sorry, we're out of pets right now.\n", ch );
	return;
    }
    else
    {
		std::string arg;
		CHAR_DATA *keeper;
		OBJ_DATA *obj;
		int cost;
		int oref = 0;
		bool found;

		one_argument( argument, arg );

		if ( ( keeper = find_keeper( ch ) ) == NULL )
			return;

		found = FALSE;
		for ( obj = keeper->last_carrying; obj; obj = obj->prev_content )
		{
			if ( obj->wear_loc == WEAR_NONE
			&&   can_see_obj( ch, obj ) )
			{
			oref++;
			if ( ( cost = get_cost( ch, keeper, obj, TRUE ) ) > 0
			&& ( arg.empty() || nifty_is_name_prefix( arg, obj->name ) ) )
			{
			if ( !found )
			{
				found = TRUE;
				send_to_char( "[Price] {ref} Item\n", ch );
			}
			if ( obj->item_type == ITEM_FIGHTERCOMP )
			{
			ch_printf( ch, "[%5d] {%3d} %s (Fighter)\n                %s Mod: %d Size: %d Cond: %d%%.\n",
			cost, oref, capitalize( obj->short_descr ).c_str(),
			show_mod_type2( obj->value[1] ),
			obj->value[3], obj->value[2], obj->value[0] );
			continue;
			}

			if ( obj->item_type == ITEM_MIDCOMP )
			{
			ch_printf( ch, "[%5d] {%3d} %s (Midsize)\n                %s Mod: %d Size: %d Cond: %d%%.\n",
			cost, oref, capitalize( obj->short_descr ).c_str(),
			show_mod_type2( obj->value[1] ),
			obj->value[3], obj->value[2], obj->value[0] );
			continue;
			}

			if ( obj->item_type == ITEM_CAPITALCOMP )
			{
			ch_printf( ch, "[%5d] {%3d} %s (Capital)\n                %s Mod: %d Size: %d Cond: %d%%.\n",
			cost, oref, capitalize( obj->short_descr ).c_str(),
			show_mod_type2( obj->value[1] ),
			obj->value[3], obj->value[2], obj->value[0] );
			continue;
			}

			ch_printf( ch, "[%5d] {%3d} %s%s.\n",
			cost, oref, capitalize( obj->short_descr ).c_str(),
				BV_IS_SET(obj->objflags, ITEM_HUTT_SIZE) ? " (hutt size)" :
				( BV_IS_SET(obj->objflags, ITEM_LARGE_SIZE) ? " (large)" :
				( BV_IS_SET(obj->objflags, ITEM_HUMAN_SIZE) ? " (medium)" :
				( BV_IS_SET(obj->objflags, ITEM_SMALL_SIZE) ? " (small)" :
				"" ) ) ) );
			}
			}
		}

		if ( !found )
		{
			if ( arg.empty() )
			send_to_char( "You can't buy anything here.\n", ch );
			else
			send_to_char( "You can't buy that here.\n", ch );
		}
		return;
    }
}


void do_sell( CHAR_DATA *ch, char *argument )
{
    std::string buf;
    std::string arg;
    CHAR_DATA *keeper;
    OBJ_DATA *obj;
    int cost;

    one_argument( argument, arg );

    if ( arg.empty() )
    {
	send_to_char( "Sell what?\n", ch );
	return;
    }

    if ( ( keeper = find_keeper( ch ) ) == NULL )
	return;

    if ( ( obj = get_obj_carry( ch, arg ) ) == NULL )
    {
	act( AT_TELL, "$n tells you 'You don't have that item.'",
		keeper, NULL, ch, TO_VICT );
	ch->reply = keeper;
	return;
    }

    if ( !can_drop_obj( ch, obj ) )
    {
	send_to_char( "You can't let go of it!\n", ch );
	return;
    }

    if ( obj->timer > 0 )
    {
	act( AT_TELL, "$n tells you, '$p is depreciating in value too quickly...'", keeper, obj, ch, TO_VICT );
	return;
    }

    if ( ( cost = get_cost( ch, keeper, obj, FALSE ) ) <= 0 )
    {
	act( AT_ACTION, "$n looks uninterested in $p.", keeper, obj, ch, TO_VICT );
	return;
    }

    if ( ch->gold > INT_MAX - cost )
    {
        send_to_char("You cannot carry any more credits.\n", ch);
        return;
    }

    AREA_DATA *area = ch->in_room->area;
    int cargo_supply = 0;
    if ( obj->item_type == ITEM_CARGO )
    {
        int type = obj->value[0];
        if ( !area || type < 0 || type >= static_cast<int>(sizeof(area->supply) / sizeof(area->supply[0]))
             || obj->value[1] <= 0 || area->supply[type] > INT_MAX - obj->value[1] )
        {
            send_to_char("The shopkeeper cannot accept that cargo.\n", ch);
            return;
        }
        cargo_supply = area->supply[type] + obj->value[1];
    }

    const bool spice = obj->item_type == ITEM_SPICE;
    const int from_keeper = UMIN(cost, UMAX(0, keeper->gold));
    const int from_economy = cost - from_keeper;
    if ( from_economy && (!area || (!spice && !economy_has(area, from_economy))) )
    {
        act(AT_ACTION, "$n can not afford $p right now.", keeper, obj, ch, TO_VICT);
        return;
    }
    if ( from_economy )
    {
        act(AT_TELL, "$n makes a credit transaction.", keeper, obj, ch, TO_VICT);
        lower_economy(area, from_economy);
    }

    separate_obj( obj );
    act( AT_ACTION, "$n sells $p.", ch, obj, NULL, TO_ROOM );
    buf = str_printf( "You sell $p for %d credit%s.",
	cost, cost == 1 ? "" : "s" );
    act( AT_ACTION, buf.c_str(), ch, obj, NULL, TO_CHAR );
    ch->gold     += cost;
    keeper->gold -= from_keeper;
    if ( spice )
        boost_economy(area, static_cast<int>(UMIN(INT_MAX, 3LL * cost / 2)));
    if ( obj->item_type == ITEM_CARGO )
        area->supply[obj->value[0]] = cargo_supply;

    if ( obj->item_type == ITEM_TRASH )
	extract_obj( obj );
    else  if ( BV_IS_SET( obj->objflags , ITEM_CONTRABAND) )
   {
       long ch_exp;
       
       ch_exp = UMIN( obj->cost*10 , ( exp_level( ch->skill_level[SMUGGLING_ABILITY]+1) - exp_level( ch->skill_level[SMUGGLING_ABILITY])  ) / 10  );
       ch_printf( ch, "You receive %ld smuggling experience for unloading your contraband.\n " , ch_exp );
       gain_exp( ch, ch_exp , SMUGGLING_ABILITY );
       if ( obj->item_type == ITEM_SPICE || obj->item_type == ITEM_RAWSPICE )
	 extract_obj( obj );
       else
       {
         BV_REMOVE_BIT( obj->objflags , ITEM_CONTRABAND );
         obj_from_char( obj );
         obj_to_char( obj, keeper );
       }
   }
    else if ( obj->item_type == ITEM_SPICE || obj->item_type == ITEM_RAWSPICE )
	extract_obj( obj );
    else
    {
	obj_from_char( obj );
	obj_to_char( obj, keeper );
    }

    return;
}



void do_value( CHAR_DATA *ch, char *argument )
{
    std::string buf;
    CHAR_DATA *keeper;
    OBJ_DATA *obj;
    int cost;

    if ( argument[0] == '\0' )
    {
	send_to_char( "Value what?\n", ch );
	return;
    }

    if ( ( keeper = find_keeper( ch ) ) == NULL )
	return;

    if ( ( obj = get_obj_carry( ch, argument ) ) == NULL )
    {
	act( AT_TELL, "$n tells you 'You don't have that item.'",
		keeper, NULL, ch, TO_VICT );
	ch->reply = keeper;
	return;
    }

    if ( !can_drop_obj( ch, obj ) )
    {
	send_to_char( "You can't let go of it!\n", ch );
	return;
    }

    if ( ( cost = get_cost( ch, keeper, obj, FALSE ) ) <= 0 )
    {
	act( AT_ACTION, "$n looks uninterested in $p.", keeper, obj, ch, TO_VICT );
	return;
    }

    buf = str_printf( "$n tells you 'I'll give you %d credits for $p.'", cost );
    act( AT_TELL, buf.c_str(), keeper, obj, ch, TO_VICT );
    ch->reply = keeper;

    return;
}

/*
 * Repair a single object. Used when handling "repair all" - Gorog
 */
void repair_one_obj( CHAR_DATA *ch, CHAR_DATA *keeper, OBJ_DATA *obj,
                 char *arg, int maxgold )
{
   std::string buf;
   long long cost;
   const char *fixstr = "repair";
   const char *fixstr2 = "repairs";

   if ( keeper && keeper->pIndexData && keeper->pIndexData->pShop )
   {
       const FLAG_SET &stype = keeper->pIndexData->pShop->shop_type;
       if ( obj->item_type == ITEM_DEVICE
       ||   ( BV_IS_SET(stype, SHOP_RECHARGE) && !BV_IS_SET(stype, SHOP_FIX) ) )
       {
           fixstr = "recharge";
           fixstr2 = "recharges";
       }
   }

   	if ( !can_drop_obj( ch, obj ) )
       	ch_printf( ch, "You can't let go of %s.\n", obj->name );
   	else if ( ( cost = get_repaircost( keeper, obj ) ) < 0 )
   	{
       	if (cost != -2)
       		act( AT_TELL, "$n tells you, 'Sorry, I can't do anything with $p.'", 
            	keeper, obj, ch, TO_VICT );
       	else
	  		act( AT_TELL, "$n tells you, '$p looks fine to me!'", keeper, obj, ch, TO_VICT );
   	}
               /* "repair all" gets a 10% surcharge - Gorog */

	else if ( (cost = strcmp("all",arg) ? cost : 11*cost/10) > ch->gold )
	{
		buf = str_printf(
		"$N tells you, 'It will cost %lld credit%s to %s %s...'", cost,
		cost == 1 ? "" : "s", fixstr, obj->name );
		act( AT_TELL, buf, ch, NULL, keeper, TO_CHAR );
		act( AT_TELL, "$N tells you, 'Which I see you can't afford.'", ch,
				NULL, keeper, TO_CHAR );
	}
	else
	{
		buf = str_printf( "$n gives $p to $N, who quickly %s it.", fixstr2 );
		act( AT_ACTION, buf.c_str(), ch, obj, keeper, TO_ROOM );
		buf = str_printf( "$N charges you %lld credit%s to %s $p.",
			cost, cost == 1 ? "" : "s", fixstr );
		act( AT_ACTION, buf.c_str(), ch, obj, keeper, TO_CHAR );
		ch->gold -= static_cast<int>(cost);
		const long long keeper_gold = keeper->gold + cost;
		if ( keeper_gold > maxgold )
		{
			long long deposit = keeper_gold - maxgold/2;
			while ( deposit > INT_MAX )
			{
				boost_economy( keeper->in_room->area, INT_MAX );
				deposit -= INT_MAX;
			}
			boost_economy( keeper->in_room->area, static_cast<int>(deposit) );
			keeper->gold = maxgold/2;
			act( AT_ACTION, "$n puts some credits into a large safe.", keeper, 
		NULL, NULL, TO_ROOM );
		}
		else
			keeper->gold = static_cast<int>(std::max(0LL, keeper_gold));

		switch ( obj->item_type )
		{
			default:
			send_to_char( "For some reason, you think you got ripped off...\n", ch);
			break;
			case ITEM_ARMOR:
			obj->value[0] = obj->value[1];
			break;
			case ITEM_WEAPON:
			obj->value[0] = INIT_WEAPON_CONDITION;
			break;
			case ITEM_DEVICE:
			obj->value[2] = obj->value[1];
			break;
		}

		oprog_repair_trigger( ch, obj );
	}
}

void do_repair( CHAR_DATA *ch, char *argument )
{
    CHAR_DATA *keeper;
    OBJ_DATA *obj;
    int maxgold;

    if ( argument[0] == '\0' )
    {
	send_to_char( "Repair what?\n", ch );
	return;
    }

    if ( ( keeper = find_fixer( ch ) ) == NULL )
	return;

    maxgold = keeper->top_level * 10;

    if ( !strcmp( argument, "all" ) )
    {
	OBJ_DATA *obj_next;

	for ( obj = ch->first_carrying; obj ; obj = obj_next )
	{
           obj_next = obj->next_content;
           if ( obj->wear_loc  == WEAR_NONE
           &&   can_see_obj( ch, obj )
	   && ( obj->item_type == ITEM_ARMOR
	   ||   obj->item_type == ITEM_WEAPON
	   ||   obj->item_type == ITEM_DEVICE ) )
                repair_one_obj( ch, keeper, obj, argument, maxgold);
	}
    return;
    }

    if ( ( obj = get_obj_carry( ch, argument ) ) == NULL )
    {
	act( AT_TELL, "$n tells you 'You don't have that item.'",
		keeper, NULL, ch, TO_VICT );
	ch->reply = keeper;
	return;
    }

    repair_one_obj( ch, keeper, obj, argument, maxgold); }

void appraise_all( CHAR_DATA *ch, CHAR_DATA *keeper, const std::string& fixstr )
{
    OBJ_DATA *obj;
    std::string buf;
    int cost;
    long long total = 0;

    for ( obj = ch->first_carrying; obj != NULL ; obj = obj->next_content )
    {
        if ( obj->wear_loc  == WEAR_NONE
        &&   can_see_obj( ch, obj )
        && ( obj->item_type == ITEM_ARMOR
        ||   obj->item_type == ITEM_WEAPON
        ||   obj->item_type == ITEM_DEVICE ) )
        {

            if ( !can_drop_obj( ch, obj ) )
            ch_printf( ch, "You can't let go of %s.\n", obj->name );
            else if ( ( cost = get_repaircost( keeper, obj ) ) < 0 )
            {
               if (cost != -2)
               act( AT_TELL,
                    "$n tells you, 'Sorry, I can't do anything with $p.'",
                    keeper, obj, ch, TO_VICT );
               else
               act( AT_TELL, "$n tells you, '$p looks fine to me!'",
                    keeper, obj, ch, TO_VICT );
            }
            else 
            {
            const char *item_fix = ( obj->item_type == ITEM_DEVICE ) ? "recharge" : fixstr.c_str();
            buf = str_printf(
            "$N tells you, 'It will cost %d credit%s to %s %s'",
            cost, cost == 1 ? "" : "s", item_fix, obj->name );
            act( AT_TELL, buf, ch, NULL, keeper, TO_CHAR );
            total += cost;
            }
        }
    }
    if ( total > 0 )
    {
       send_to_char ("\n", ch);
       buf = str_printf(
          "$N tells you, 'It will cost %lld credit%s in total.'",
          total, total == 1 ? "" : "s" );
       act( AT_TELL, buf, ch, NULL, keeper, TO_CHAR );
       buf = "$N tells you, 'Remember there is a 10% surcharge for repair all.'";
       act( AT_TELL, buf, ch, NULL, keeper, TO_CHAR );
    }
}
	

void do_appraise( CHAR_DATA *ch, char *argument )
{
    std::string buf;
    std::string arg;
    CHAR_DATA *keeper;
    OBJ_DATA *obj;
    int cost;
    std::string fixstr;

    one_argument( argument, arg );

    if ( arg.empty() )
    {
		send_to_char( "Appraise what?\n", ch );
		return;
    }

    if ( ( keeper = find_fixer( ch ) ) == NULL )
		return;

    fixstr = "repair";
    if ( keeper->pIndexData && keeper->pIndexData->pShop )
    {
        const FLAG_SET &stype = keeper->pIndexData->pShop->shop_type;
        if ( BV_IS_SET(stype, SHOP_RECHARGE) && !BV_IS_SET(stype, SHOP_FIX) )
            fixstr = "recharge";
    }

    if ( !str_cmp( arg, "all") )
    {
		appraise_all( ch, keeper, fixstr );
		return;
    }

    if ( ( obj = get_obj_carry( ch, arg ) ) == NULL )
    {
	act( AT_TELL, "$n tells you 'You don't have that item.'",
		keeper, NULL, ch, TO_VICT );
	ch->reply = keeper;
	return;
    }

    if ( !can_drop_obj( ch, obj ) )
    {
	send_to_char( "You can't let go of it.\n", ch );
	return;
    }

    if ( ( cost = get_repaircost( keeper, obj ) ) < 0 )
    {
      if (cost != -2)
	act( AT_TELL, "$n tells you, 'Sorry, I can't do anything with $p.'", keeper, obj, ch, TO_VICT );
      else
	act( AT_TELL, "$n tells you, '$p looks fine to me!'", keeper, obj, ch, TO_VICT );
      return;
    }

    fixstr = ( obj->item_type == ITEM_DEVICE ) ? "recharge" : "repair";
    buf = str_printf(
       "$N tells you, 'It will cost %d credit%s to %s that...'", cost,
       cost == 1 ? "" : "s", fixstr.c_str() );
    act( AT_TELL, buf, ch, NULL, keeper, TO_CHAR );
    if ( cost > ch->gold )
      act( AT_TELL, "$N tells you, 'Which I see you can't afford.'", ch,
	 NULL, keeper, TO_CHAR );

    return;
}


/* ------------------ Shop Building and Editing Section ----------------- */


static void initialize_shop(GameContext *game, MOB_INDEX_DATA *mob)
{
    SHOP_DATA *shop;
    CREATE( shop, SHOP_DATA, 1 );
	shop->game = game;
    LINK( shop, first_shop, last_shop, next, prev );
    shop->keeper	= mob->vnum;
    shop->profit_buy	= 120;
    shop->profit_sell	= 90;
    shop->profit_fix	= 100;
    BV_SET_BIT(shop->shop_type, SHOP_BUY);
    shop->open_hour	= 0;
    shop->close_hour	= 23;
    mob->pShop		= shop;
    top_shop++;
}

void do_makeshop( CHAR_DATA *ch, char *argument )
{
    int vnum;
    MOB_INDEX_DATA *mob;

    if ( !argument || argument[0] == '\0' )
    {
	send_to_char( "Usage: makeshop <mobvnum>\n", ch );
	return;
    }

    vnum = atoi( argument );
    
    if ( (mob = get_mob_index(vnum)) == NULL )
    {
	send_to_char( "Mobile not found.\n", ch );
	return;
    }

    if ( !can_medit(ch, mob) )
      return;

    if ( mob->pShop )
    {
	send_to_char( "This mobile already has a shop.\n", ch );
	return;
    }

    initialize_shop(ch->game, mob);
    send_to_char( "Done.\n", ch );
    return;
}


void do_shopstat( CHAR_DATA *ch, char *argument )
{
    SHOP_DATA *shop;
    MOB_INDEX_DATA *mob;
    int vnum;
    
    if ( argument[0] == '\0' )
    {
	send_to_char( "Usage: shopstat <keeper vnum>\n", ch );
	return;
    }

    vnum = atoi( argument );
    
    if ( (mob = get_mob_index(vnum)) == NULL )
    {
	send_to_char( "Mobile not found.\n", ch );
	return;
    }

    if ( !mob->pShop )
    {
	send_to_char( "This mobile doesn't keep a shop.\n", ch );
	return;
    }
    shop = mob->pShop;

    ch_printf( ch, "Keeper: %d  %s\n", shop->keeper, mob->short_descr );
    ch_printf( ch, "Shops: %s\n", bitset_to_string(shop->shop_type, shop_types).c_str() );
    ch_printf( ch, "buytype: %s\n", bitset_to_string(shop->buy_type, o_types).c_str() );
    ch_printf( ch, "Profit:  buy %3d%%  sell %3d%%  fix %3d%%\n",
			shop->profit_buy,
			shop->profit_sell,
			shop->profit_fix );
    ch_printf( ch, "Hours:   open %2d  close %2d\n",
			shop->open_hour,
			shop->close_hour );
    if ( shop->separate_repair )
    {
        ch_printf( ch, "Repair types: %s\n", bitset_to_string( shop->repair_type, o_types ).c_str() );
        ch_printf( ch, "Repair hours: open %2d  close %2d\n", shop->repair_open_hour, shop->repair_close_hour );
    }
    return;
}

std::string shop_type_to_string( SHOP_DATA *shop )
{
	std::string result;
	if ( BV_IS_SET(shop->shop_type, SHOP_BUY) )
		result += "B";	
	if ( BV_IS_SET(shop->shop_type, SHOP_FIX) )
		result += "F";
	if ( BV_IS_SET(shop->shop_type, SHOP_RECHARGE) )
		result += "R";

	return result.empty() ? "Non" : result;
}


void do_shops( CHAR_DATA *ch, char *argument )
{
    SHOP_DATA *shop;

    if ( !first_shop )
    {
	send_to_char( "There are no shops.\n", ch );
	return;
    }

    set_char_color( AT_NOTE, ch );
    for ( shop = first_shop; shop; shop = shop->next )
	ch_printf( ch, "Keeper: %5d Buy: %3d Sell: %3d Fix: %3d Open: %2d Close: %2d Shops: %3s Buy: %s\n",
		shop->keeper,	   shop->profit_buy, shop->profit_sell, shop->profit_fix,
		shop->open_hour,   shop->close_hour,
		shop_type_to_string( shop ).c_str(),
		bitset_to_string(shop->buy_type, o_types).c_str() );
    return;
}


/*This is the command used to buy a contract from a vendor to place a player
owned vendor
*/
void do_buyvendor (CHAR_DATA *ch, char *argument)
{
  CHAR_DATA *keeper;
  OBJ_DATA *deed;
  std::string buf1, strsave;
  struct stat fst;

  if (IS_NPC(ch))
    return;

  strsave = str_printf("%s%s", VENDOR_DIR, capitalize(ch->name).c_str());
  const bool replace_existing = stat(strsave.c_str(), &fst) == 0;
  if ( replace_existing && str_cmp(argument, "yes") )
  {
      send_to_char("You already have a shop!\n"
                   "Type buyvendor yes to replace your old shop.\n", ch);
      return;
  }

  if (  (keeper = find_keeper_q( ch, FALSE ) )  == NULL  )
  {
    send_to_char ("There is no one to buy that from!\n", ch);
    return;
  }

  if ( ch->gold < COST_BUY_VENDOR )
     {
         buf1 = str_printf("%s says, You are too poor!\n", keeper->name);
         send_to_char (buf1, ch);
         return;
     }

if ( (ch->top_level) < LEVEL_BUY_VENDOR )
	{
		buf1 = str_printf("you must be at least %d level.\n", LEVEL_BUY_VENDOR);
		send_to_char (buf1, ch);
		return;
	}

if ( (get_obj_index (OBJ_VNUM_DEED) ) == NULL )
	{
		bug ("BUYVENDOR: Deed is missing!");
		return;
	}

deed = create_object(get_obj_index(OBJ_VNUM_DEED), 0);
if ( !deed )
    return;
if ( replace_existing && remove(strsave.c_str()) != 0 )
{
    extract_obj(deed);
    send_to_char("The old shop could not be replaced.\n", ch);
    return;
}
if ( replace_existing )
{
    for ( CHAR_DATA *old_vendor = first_char; old_vendor; old_vendor = old_vendor->next )
        if ( IS_NPC(old_vendor) && old_vendor->owner && !str_cmp(old_vendor->owner, ch->name) )
        {
            extract_char(old_vendor, TRUE);
            break;
        }
}
obj_to_char(deed, ch);
send_to_char ("&bVery well, you may have a contract for a vendor.\n", ch);
ch->gold = ch->gold - COST_BUY_VENDOR;

}


/*this is the command that places the vendor at the specified location*/
static void prepare_vendor(CHAR_DATA *vendor)
{
    MOB_INDEX_DATA *prototype = vendor->pIndexData;
    std::string description = prototype->long_descr ? prototype->long_descr : "";
    const auto marker = description.find("%s");
    if ( marker != std::string::npos )
        description.replace(marker, 2, vendor->owner);
    STRFREE(vendor->long_descr);
    vendor->long_descr = STRALLOC(description);
}

void do_placevendor (CHAR_DATA *ch, char *argument)
{
std::string strsave;
struct stat fst;
CHAR_DATA *vendor;
MOB_INDEX_DATA *temp;
OBJ_DATA *obj;
if ( !ch || IS_NPC(ch) || !ch->in_room )
    return;
for ( CHAR_DATA *occupant = ch->in_room->first_person; occupant; occupant = occupant->next_in_room )
    if ( IS_NPC(occupant) && occupant->pIndexData
         && (occupant->owner || (occupant->pIndexData->pShop
             && BV_IS_SET(occupant->pIndexData->pShop->shop_type, SHOP_BUY))) )
    {
        send_to_char("A vendor is already here!\n", ch);
        return;
    }

/* better way to do this? what if they have another object called deed?*/
  if ( ( obj = get_obj_carry( ch, "deed" ) ) == NULL )
	{
	    send_to_char( "You do not have a deed!.\n", ch );
	    return;
	}

  if (obj->pIndexData->vnum != OBJ_VNUM_DEED)
	{
	    send_to_char( "You do not have a deed!.\n", ch );
	    return;
	}

	if (!BV_IS_SET(ch->in_room->room_flags, ROOM_PLR_SHOP) )
	{
		send_to_char( "You need to find a free shop.\n", ch);
		return;
	}

        strsave = str_printf( "%s/%s", VENDOR_DIR, capitalize( ch->name ).c_str() );

    	if ( stat( strsave.c_str(), &fst ) != -1 )
    	{
		send_to_char( "You already have a shop!\n", ch);
		return;
	}



if ( (temp = get_mob_index (MOB_VNUM_VENDOR) ) == NULL )
{
	log_string ("do_placevendor: no vendor vnum");
	return;
}

if (!ch->game)
{
    send_to_char( "You must be in a game to place a vendor.\n", ch );
    return;
}

vendor = create_mobile(ch->game, temp);
if ( !vendor )
    return;
vendor->owner = STRALLOC(ch->name);
vendor->home = ch->in_room;
prepare_vendor(vendor);
char_to_room(vendor, ch->in_room);

save_vendor (vendor);

separate_obj( obj );
extract_obj( obj );

act( AT_ACTION, "$n appears in a swirl of smoke.\n", vendor, NULL, NULL, TO_ROOM );

}


void do_pricevendor(CHAR_DATA *ch, char *argument)
{
    if ( !ch || IS_NPC(ch) )
        return;

    std::string item, price_argument;
    one_argument(one_argument(argument, item), price_argument);
    if ( item.empty() || price_argument.empty() )
    {
        send_to_char("Usage: pricevendor <item> <cost>\n", ch);
        return;
    }

    CHAR_DATA *vendor = find_keeper(ch);
    if ( !vendor )
        return;
    if ( !vendor->owner || str_cmp(ch->name, vendor->owner) )
    {
        send_to_char("This isn't your vendor!\n", ch);
        return;
    }
    if ( ch->fighting )
    {
        send_to_char("You can't do that while fighting.\n", ch);
        return;
    }

    char *end;
    errno = 0;
    const long price = strtol(price_argument.c_str(), &end, 10);
    if ( errno == ERANGE || *end || price < 1 || price > INT_MAX )
    {
        send_to_char("Enter a positive price in credits.\n", ch);
        return;
    }

    OBJ_DATA *obj = get_obj_carry(vendor, item);
    if ( !obj )
    {
        send_to_char("Your vendor doesn't have that item.\n", ch);
        return;
    }
    obj->cost = static_cast<int>(price);
    save_vendor(vendor);
    send_to_char("The price has been changed.\n", ch);
}

void do_collectgold(CHAR_DATA *ch, char *argument)
{
    if ( !ch || IS_NPC(ch) )
        return;

    CHAR_DATA *vendor = find_keeper(ch);
    if ( !vendor )
        return;
    if ( !vendor->owner || str_cmp(ch->name, vendor->owner) )
    {
        send_to_char("This isn't your vendor!\n", ch);
        return;
    }
    if ( ch->fighting )
    {
        send_to_char("You can't do that while fighting.\n", ch);
        return;
    }
    if ( vendor->gold <= 0 )
    {
        send_to_char("Your vendor has no earnings to collect.\n", ch);
        return;
    }

    const int fee = vendor->gold / VENDOR_FEE_DIVISOR;
    const int payout = vendor->gold - fee;
    if ( ch->gold > INT_MAX - payout )
    {
        send_to_char("You cannot carry that many credits.\n", ch);
        return;
    }

    if ( vendor->in_room && vendor->in_room->area )
        boost_economy(vendor->in_room->area, fee);
    vendor->gold = 0;
    ch->gold += payout;
    save_vendor(vendor);

    send_to_char("&GYour vendor gladly hands over his earnings minus a small fee of course..\n", ch);
    act(AT_ACTION, "$n hands over some money.\n", vendor, NULL, NULL, TO_ROOM);
}

/* Write vendor to file */
void fwrite_vendor( FILE *fp, CHAR_DATA *mob )
{
  if ( !IS_NPC( mob ) || !fp )
	return;
  fprintf( fp, "Vnum     %d\n", mob->pIndexData->vnum );
  if (mob->gold > 0)
  fprintf (fp, "Gold     %d\n",mob->gold);
  if ( mob->home )
  fprintf( fp, "Home     %d\n", mob->home->vnum );
  if (mob->owner != NULL )
  fprintf (fp, "Owner     %s~\n", mob->owner );
  if ( str_cmp( mob->short_descr, mob->pIndexData->short_descr) )
  	fprintf( fp, "Short	    %s~\n", mob->short_descr );
  fprintf( fp, "Position   %d\n", mob->position );
  fwrite_bitset(fp, "FlagsEx",mob->act);
//  fprintf( fp, "Flags   %d\n",   mob->act );
fprintf( fp, "END\n" );

  return;
}


/* read vendor from file */
CHAR_DATA *  fread_vendor( GameContext *game, FILE *fp )
{
    if ( !game || !fp )
        return nullptr;

    const char *word = fread_word(fp);
    if ( str_cmp(word, "Vnum") )
    {
        bug("Fread_vendor: Vnum not found");
        return nullptr;
    }

    const int vnum = fread_number(fp);
    MOB_INDEX_DATA *prototype = get_mob_index(vnum);
    if ( !prototype )
    {
        bug("Fread_vendor: No index data for vnum %d", vnum);
        return nullptr;
    }

    CHAR_DATA *mob = create_mobile(game, prototype);
    if ( !mob )
        return nullptr;
    int inroom = 0;
    bool fMatch;
    ObjectNest nest{};
  for ( ; ;) {
       word = feof(fp) ? "" : fread_word(fp);
       if ( feof(fp) && !*word )
       {
           bug("Fread_vendor: missing END");
           extract_char(mob, TRUE);
           return nullptr;
       }
       fMatch = FALSE;
       switch ( UPPER(word[0]) ) {
	case '*':
           fMatch = TRUE;
           fread_to_eol( fp );
           break;
	case '#':
		if ( !strcmp( word, "#OBJECT" ) )
			{
			fread_obj(game, mob, fp, OS_CARRY, nest);
                fMatch = TRUE;
			}
			break;
	case 'D':
		KEY( "Description", mob->description, fread_string(fp));
		break;
	case 'E':

		if ( !strcmp( word, "END" ) )
		{
            ROOM_INDEX_DATA *room = get_room_index(inroom ? inroom : ROOM_VNUM_VENSTOR);
            if ( !room )
                room = get_room_index(ROOM_VNUM_VENSTOR);
            if ( !room || !mob->owner || !*mob->owner )
            {
                bug("Fread_vendor: missing valid home or owner for vnum %d", vnum);
                extract_char(mob, TRUE);
                return nullptr;
            }

            for ( CHAR_DATA *occupant = room->first_person; occupant; occupant = occupant->next_in_room )
                if ( IS_NPC(occupant) && occupant->owner )
                {
                    bug("Fread_vendor: room %d already has a vendor", room->vnum);
                    extract_char(mob, TRUE);
                    return nullptr;
                }

            mob->home = room;
            prepare_vendor(mob);
            char_to_room(mob, room);
		 mob->hit = 10000;
		 mob->max_hit = 10000;
		return mob;
		}
		break;
 	case 'F':
		//KEY( "Flags", mob->act, fread_number(fp));
		if ( !str_cmp( word, "Flags"  ) )
		{
			mob->act = int_to_bitset(fread_number( fp ));
			fMatch = TRUE;
			break;
		}
		if ( !str_cmp( word, "FlagsEx"  ) )
		{
			fread_bitset(fp, mob->act);
			fMatch = TRUE;
			break;
		}		
		break;
	case 'G':
	KEY("Gold", mob->gold, fread_number(fp));
	break;
   case 'H':
	  KEY("Home", inroom, fread_number(fp) );
     break;
	case 'L':
		break;
	case 'N':
		break;
	case 'O':
		KEY ("Owner", mob->owner, fread_string (fp) );
		break;
	case 'P':
		KEY( "Position", mob->position, fread_number( fp ) );
		break;
	case 'S':
		KEY( "Short", mob->short_descr, fread_string(fp));
		break;
	}
	if ( !fMatch )
	{
	   bug ( "Fread_vendor: no match." );
	   bug ( "%s", word );
	}
  }
  return NULL;
}




void save_vendor( CHAR_DATA *ch )
{
    std::string strsave;
    FILE *fp;

    if ( !ch || !ch->owner || !*ch->owner )
    {
	bug("Save_vendor: missing vendor or owner");
	return;
    }

    EquipmentSlots equipment;
    de_equip_char(ch, equipment);


    strsave = str_printf("%s%s",VENDOR_DIR, capitalize( ch->owner ).c_str() );

    if ( ( fp = fopen( strsave.c_str(), "w" ) ) == NULL )
    {
	perror( strsave.c_str() );
	bug( "Save_vendor: fopen" );
    }
    else
    {
	bool ferr;

	chmod(strsave.c_str(), S_IRUSR|S_IWUSR | S_IRGRP|S_IWGRP | S_IROTH|S_IWOTH);
	fprintf( fp, "#VENDOR\n"		);
	fwrite_vendor( fp, ch );

	if ( ch->first_carrying )
	fwrite_obj(ch, ch->last_carrying, fp, 0, OS_CARRY, &equipment);

	fprintf(fp, "#END\n" );
	ferr = ferror(fp);
	FCLOSE( fp );
	if (ferr)
	{
	  perror(strsave.c_str());
	  bug("Error writing temp file for %s -- not copying", strsave.c_str());
	}
    }

    re_equip_char(ch, equipment);
    return;
}

