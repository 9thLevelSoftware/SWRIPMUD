CC      = g++
PROF    = 
#Uncomment the next line if you want MCCP support
DBUGFLG = -DMCCP
C_FLAGS = -g3 -Wall -Wextra -Wformat=2 -Wshift-overflow=2 -Wimplicit-fallthrough=2 -fno-omit-frame-pointer -MMD -MP -Wno-write-strings -fsanitize=address,undefined $(PROF) $(DBUGFLG)
L_FLAGS = -fsanitize=address,undefined -fno-omit-frame-pointer $(PROF)

C_FILES = act_comm.c act_info.c act_move.c act_obj.c act_wiz.c boards.c \
	  build.c clans.c comm.c comments.c const.c db.c fight.c \
	  handler.c hashstr.c id.c interp.c magic.c makeobjs.c \
	  misc.c mud_comm.c mud_prog.c krearena.c player.c \
	  reset.c save.c shops.c skills.c special.c tables.c track.c update.c \
	  space.c space2.c bounty.c swskills.c alias.c grub.c mapper.c templateparse.c \
	  trivia.c gmcp.c olc.c olc2.c craft.c

O_FILES = $(C_FILES:.c=.o)

H_FILES = mud.h alias.h bet.h craft.h olc.h space2.h trivia.h

all: swrip
	mkdir -p ../bin
	mv swrip ../bin/swrip

swrip: $(O_FILES)
	rm -f swrip
	$(CC) $(L_FLAGS) -o swrip $(O_FILES)  -lz -lm -lssl -lcrypto -lcrypt
	chmod g+w swrip
	chmod g+w $(O_FILES)

.c.o:
	$(CC) -c $(C_FLAGS) $< -o $@

clean:
	rm -f $(O_FILES) $(O_FILES:.o=.d) swrip

-include $(O_FILES:.o=.d)
