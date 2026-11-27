#include "trivia.h"
#include <string.h>

struct trivia_struct *g_trivia = NULL;

static void trivia_clear_game(void)
{
  struct winner_struct *ws;
  struct winner_struct *ws2;
  struct player_struct *p;
  struct player_struct *p2;

  if (g_trivia == NULL)
    return;

  ws = g_trivia->winners;
  while (ws)
  {
    ws2 = ws;
    ws = ws->next;
    DISPOSE(ws2);
  }
  g_trivia->winners = NULL;

  p = g_trivia->players;
  while (p)
  {
    p2 = p;
    p = p->next;
    DISPOSE(p2);
  }
  g_trivia->players = NULL;

  DISPOSE(g_trivia);
}

void trivia_remove_char(CHAR_DATA *ch)
{
  struct player_struct *p;
  struct player_struct *p_prev = NULL;
  struct player_struct *p_next;
  struct winner_struct *w;
  struct winner_struct *w_prev = NULL;
  struct winner_struct *w_next;

  if (ch == NULL || g_trivia == NULL)
    return;

  for (p = g_trivia->players; p; p = p_next)
  {
    p_next = p->next;
    if (p->ch == ch)
    {
      if (p_prev == NULL)
        g_trivia->players = p_next;
      else
        p_prev->next = p_next;
      DISPOSE(p);
    }
    else
    {
      p_prev = p;
    }
  }

  for (w = g_trivia->winners; w; w = w_next)
  {
    w_next = w->next;
    if (w->ch == ch)
    {
      if (w_prev == NULL)
        g_trivia->winners = w_next;
      else
        w_prev->next = w_next;
      DISPOSE(w);
    }
    else
    {
      w_prev = w;
    }
  }

  if (g_trivia->asker == ch)
  {
    g_trivia->asker = NULL;
    send_to_trivia("The trivia host has left; the trivia game has ended!\n");
    trivia_clear_game();
  }
}

void do_trivia(CHAR_DATA *ch, char *argument)
 {
   std::string arg0;
   std::string arg1;
   std::string argstr = argument ? argument : "";
   std::string buf;
   std::string buf2;
   int i;

   struct winner_struct *ws;
   OBJ_INDEX_DATA *pObjIndex;

   if (!ch)
     return;

   argstr = one_argument(argstr,arg0);
   argstr = one_argument(argstr,arg1);

   if (IS_NPC(ch))
     {
       send_to_char("Mobs can't start a trivia game.\n",ch);
       return;
     }
   
   if (!IS_IMMORTAL(ch))
     {
       send_to_char("You must be immortal to run a trivia game.\n",ch);
       return;
     }

   if(arg0.empty())
   {
      send_to_char("Usage: trivia <start | end> [reward]\n",ch);
      return;
   }

   if (!str_cmp(arg0,"start")) 
     {
       if (g_trivia != NULL) 
	 {
	   send_to_char("There is currently a trivia game in progress.\n",ch);
	   return;
	 }
       CREATE(g_trivia,struct trivia_struct,1);
       g_trivia->game = ch->game;
       g_trivia->current_question = 0;
       g_trivia->asker = ch;
       g_trivia->winners = NULL;
       g_trivia->players = NULL;
       if (!arg1.empty() && is_number(arg1)) 
	 {
	   g_trivia->prize = strtoi(arg1); 
	   pObjIndex = get_obj_index( g_trivia->prize );
	   if (pObjIndex == NULL)
	     {
	       send_to_char("That object does not exist.\n",ch);
	       DISPOSE(g_trivia);
	       return;	       
	     }
	   send_to_char("Use tquestion to ask a question\nUse twinner to announce the winner\nUse trivia end to end the game\n",ch);
	   echo_to_all(AT_WHITE, "A new trivia game has begun!\nType &Ctjoin&W to play!\n", ECHOTAR_ALL); 
	   echo_to_all(AT_WHITE, str_printf("Todays prize is %s!\n", pObjIndex->short_descr ? pObjIndex->short_descr : "a prize"), ECHOTAR_ALL);
	 }
       else
	 {
	   g_trivia->prize = -1;
	   echo_to_all(AT_WHITE, "A new trivia game has begun!\nType &Ctjoin&W to play!\n", ECHOTAR_ALL); 	   
	   echo_to_all(AT_WHITE, "Todays prize is a mystery!\n", ECHOTAR_ALL);
	 }
     } 
   else if (!str_cmp(arg0,"end"))
     {
       if (g_trivia == NULL)
	 {
	   send_to_char("No trivia game in progress.\n",ch);
	   return;
	 }

       send_to_trivia("The Trivia game has ended!\n");

       buf = "Trivia Standings\n";
       
       i = 0;
       for(ws=g_trivia->winners; ws; ws = ws->next)
	 {
	   if (ws->ch == NULL || char_died(ws->ch))
	     continue;
	   i++;
	   buf2 = str_printf("%2d. %-15s ",i,ws->ch->name ? ws->ch->name : "Unknown");
	   buf += buf2;
	   if(i % 3 == 0)
	     buf += "\n";
	 }
       buf += "\n";
       send_to_trivia(buf);
       trivia_clear_game();
     }
   else
     {
       send_to_char("Usage: trivia <start | end> [reward]\n",ch);
       return;
     }
 }

void send_to_trivia(const std::string &string)
{
  struct player_struct *ch;
  if (g_trivia == NULL) 
    return;
  if (g_trivia->asker != NULL && !char_died(g_trivia->asker))
    send_to_char(string, g_trivia->asker);
  for(ch = g_trivia->players; ch; ch=ch->next) 
    {
      if (ch->ch != NULL && !char_died(ch->ch))
	send_to_char(string,ch->ch);
    }
}

bool is_trivia_player(CHAR_DATA *ch)
{
  struct player_struct *vch;

  if (ch == NULL || char_died(ch) || g_trivia == NULL)
    return false;
  if (ch == g_trivia->asker)
    return true;
  for(vch = g_trivia->players; vch; vch=vch->next)
    {
      if (vch->ch==ch)
	return true;
    }
  return false;
}


void do_trivia_score(CHAR_DATA *ch, char *argument)
{
  struct winner_struct *ws;
  std::string buf;
  std::string buf2;
  int i;

  (void)argument;

  if (!ch)
    return;
  if (g_trivia == NULL)
    {
      send_to_char("No trivia game in progress.\n",ch);
      return;
    }
  if (IS_NPC(ch))
    {
      send_to_char("Mobs can't play trivia.\n",ch);
      return;
    }
  if (!is_trivia_player(ch))
    {
      send_to_char("You are not in the trivia game.\n",ch);
      return;
    }

  buf = "Trivia Standings\n";
  
  i = 0;
  for(ws=g_trivia->winners; ws; ws = ws->next)
    {
      if (ws->ch == NULL || char_died(ws->ch))
	continue;
      i++;
      buf2 = str_printf("%2d. %-15s ",i, ws->ch->name ? ws->ch->name : "Unknown");
      buf += buf2;
      if(i % 3 == 0)
	buf += "\n";
    }
  buf += "\n";
  send_to_char(buf,ch);
}

void do_trivia_chat(CHAR_DATA *ch, char *argument)
{
  std::string buf;
  if (!ch)
    return;
  if (g_trivia == NULL)
    {
      send_to_char("No trivia game in progress.\n",ch);
      return;
    }
  if (IS_NPC(ch))
    {
      send_to_char("Mobs can't play trivia.\n",ch);
      return;
    }
  if (!is_trivia_player(ch))
    {
      send_to_char("You are not in the trivia game.\n",ch);
      return;
    }
  if (!argument || argument[0] == '\0')
    {
      send_to_char("Say what in trivia chat?\n",ch);
      return;
    }
  buf = str_printf("&C<<&wTChat&C>> &w%s: %s\n",ch->name,argument);
  send_to_trivia(buf);
}

void do_trivia_join(CHAR_DATA *ch, char *argument)
{
  std::string buf;
  struct player_struct *p;
  (void)argument;

  if (!ch)
    return;
  if (g_trivia == NULL)
    {
      send_to_char("No trivia game in progress.\n",ch);
      return;
    }
  if (IS_NPC(ch))
    {
      send_to_char("Mobs can't play trivia.\n",ch);
      return;
    }
  if (is_trivia_player(ch))
    {
      send_to_char("You have already joined the game.\n",ch);
      return;
    }
  send_to_char("You have joined the trivia game.\nType &Ctanswer&W to answer\nType &Ctchat&W to use Trivia chat\nType &Ctscore&w to see the scores\n",ch);
  buf = str_printf("%s has joined the trivia game.\n",ch->name);
  send_to_trivia(buf);
  CREATE(p, struct player_struct, 1);
  p->ch = ch;
  p->next = g_trivia->players;
  g_trivia->players = p;
}
void do_trivia_answer(CHAR_DATA *ch, char *argument)
{
  std::string buf;
  
  if (!ch)
    return;
  if (g_trivia == NULL)
    {
      send_to_char("No trivia game in progress.\n",ch);
      return;
    }
  if (IS_NPC(ch))
    {
      send_to_char("Mobs can't play a trivia game.\n",ch);
      return;
    }
  
  if(!argument || argument[0]=='\0')
    {
      send_to_char("That is a pretty sad answer.\n",ch);
      return;
    }
  if (!is_trivia_player(ch))
    {
      send_to_char("You aren't playing trivia.\n",ch);
      return;
    }
  buf = str_printf("&C<<&WAnswer&C>> &W%s: %s\n",ch->name,argument);
  send_to_trivia(buf);
}

void do_trivia_question(CHAR_DATA *ch, char *argument)
{
  std::string buf;

  if (!ch)
    return;
  if (IS_NPC(ch))
    {
      send_to_char("Mobs can't do that.\n",ch);
      return;
    }
  if (!IS_IMMORTAL(ch))
    {
      send_to_char("You must be immortal to run a trivia game.\n",ch);
      return;
    }
  if (g_trivia == NULL) 
    {
      send_to_char("There isn't currently a trivia game running.\n",ch);
      return;
    }
  if (!argument || argument[0] == '\0')
    {
      send_to_char("What is the question?\n",ch);
      return;
    }
  if (g_trivia->asker != ch) 
    {
      send_to_char("You aren't the asker.\n",ch);
      return;
    }
  g_trivia->current_question++;
  buf = str_printf("&C<<&WQuestion %d&C>> &W%s\n",g_trivia->current_question,argument);
  send_to_trivia(buf);
}

void do_trivia_winner(CHAR_DATA *ch, char *argument)
{
  struct player_struct *winner;
  struct winner_struct *wlist;
  struct winner_struct *prev = NULL;
  struct winner_struct *ws = NULL;
  OBJ_DATA *obj;
  OBJ_INDEX_DATA *pObjIndex;
  std::string target_name;
  std::string buf;

  if (!ch)
    return;
  if (IS_NPC(ch))
    {
      send_to_char("Mobs can't do that.\n",ch);
      return;
    }
  if (!IS_IMMORTAL(ch))
    {
      send_to_char("You must be immortal to run a trivia game.\n",ch);
      return;
    }
  if (g_trivia == NULL) 
    {
      send_to_char("There isn't currently a trivia game running.\n",ch);
      return;
    }
  if (!argument || argument[0] == '\0')
    {
      send_to_char("Who got the correct answer?\n",ch);
      return;
    }
  one_argument(argument, target_name);
  if (target_name.empty())
    {
      send_to_char("Who got the correct answer?\n",ch);
      return;
    }
  if (g_trivia->asker != ch) 
    {
      send_to_char("You aren't the asker.\n",ch);
      return;
    }
  //Check to see if name given is in the trivia player list
  for(winner = g_trivia->players; winner; winner = winner->next) 
    {
      if (winner->ch == NULL || char_died(winner->ch) || winner->ch->name == NULL)
	continue;
      if (!strcasecmp(winner->ch->name,target_name.c_str())) 
	{
	  //See if they already won a question
	  prev = NULL;
	  for(wlist = g_trivia->winners; wlist; wlist=wlist->next)
	    {
	      if (winner->ch == wlist->ch) 
		{
		  if (prev == NULL)
		    g_trivia->winners = wlist->next;
		  else
		    prev->next = wlist->next;

		  wlist->correct++;
		  wlist->next = NULL;

		  if (g_trivia->winners == NULL || wlist->correct > g_trivia->winners->correct)
		    {
		      wlist->next = g_trivia->winners;
		      g_trivia->winners = wlist;
		    }
		  else
		    {
		      ws = g_trivia->winners;
		      while (ws->next != NULL && ws->next->correct >= wlist->correct)
			ws = ws->next;
		      wlist->next = ws->next;
		      ws->next = wlist;
		    }
		  break;
		}
	      prev = wlist;
	    }
	  //Nope, they haven't won a question yet, so make an entry
	  if (wlist == NULL)
	    {
	      CREATE(ws,struct winner_struct, 1);
	      ws->correct = 1;
	      ws->ch = winner->ch;
	      ws->next = NULL;
	      if (g_trivia->winners == NULL)
		{
		  g_trivia->winners = ws;
		} else {
		  for(wlist = g_trivia->winners; wlist; wlist=wlist->next)
		    {
		      if (wlist->next == NULL) {
			wlist->next = ws;
			break;
		      }  
		    }
		}
	    }
	  break;
	}
    }
  if (winner == NULL) 
    {
      send_to_char("There is no one with that name playing\n",ch);
      return;
    }
  buf = str_printf("&C<<&wWinner&C>> &w%s has won question %d!\n",winner->ch->name,g_trivia->current_question);
  send_to_trivia(buf);
//GIVE PRIZE!
  if (g_trivia->prize != -1) 
    {
      pObjIndex = get_obj_index(g_trivia->prize);
      if (pObjIndex == NULL)
	{
	  send_to_char("The trivia prize object no longer exists.\n",ch);
	  return;
	}
      obj = create_object( pObjIndex, get_trust(ch) );
      if (obj == NULL)
	{
	  send_to_char("Unable to create the trivia prize object.\n",ch);
	  return;
	}
      if ( CAN_WEAR(obj, ITEM_TAKE) )
	{
	  obj = obj_to_char( obj, winner->ch );
	  send_to_char("The trivia gods have given you a prize for your great answer!\n",winner->ch);
	}
      else
	{
	  extract_obj(obj);
	  send_to_char("The trivia prize cannot be carried (missing ITEM_TAKE).\n",ch);
	}
    }
}
