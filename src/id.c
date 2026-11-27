/***************************************************************************
*                           STAR WARS REALITY 1.0                          *
*--------------------------------------------------------------------------*
* Star Wars Reality 1.0                                                    *
* copyright (c) 1997, 1998 by Sean Cooper                                  *
* -------------------------------------------------------------------------*
* Starwars and Starwars Names copyright (c) Lucasfilm Ltd.                 *
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
*			 IDENT protocol userid lookup module		   *
****************************************************************************/

/*	Donated to Realms of Despair by Tricops, author unknown.	    */

#if defined( macintosh )
#include <types.h>
#else
#include <sys/types.h>
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <fcntl.h>
#include <arpa/telnet.h>
#include <errno.h>
#include <netdb.h>
#include <unistd.h>
#include "mud.h"

extern int   maxdesc;
static bool  nonblock     args( ( int s ) );
void  start_auth   args( ( struct descriptor_data *d ) );
void  read_auth    args( ( struct descriptor_data *d ) );
void  send_auth    args( ( struct descriptor_data *d ) );
/*
 * Initiate an IDENT connection
 */
void  start_auth( struct descriptor_data *d )
{
   struct  sockaddr_in	 sock;
   struct  servent	*serv;
   int     err;
   socklen_t tlen;

   if ( !d || d->descriptor < 0 )
     return;

   d->auth_fd = socket( AF_INET, SOCK_STREAM, 0 );
   err = errno;

   if ( d->auth_fd < 0 )
   {
     if ( err == EAGAIN || err == EMFILE || err == ENFILE )
       bug( "Can't allocate fd for authorization check (%s)", strerror( err ) );
     d->auth_fd = -1;
     d->auth_state = 0;
     if ( d->atimes < 20 )
       d->atimes++;
     return;
   }

   if ( !nonblock( d->auth_fd ) )
   {
     closed( d->auth_fd );
     d->auth_fd = -1;
     d->auth_state = 0;
     if ( d->atimes < 20 )
       d->atimes++;
     return;
   }

   memset( &sock, 0, sizeof( sock ) );
   tlen = sizeof( sock );
   if ( getpeername( d->descriptor, ( struct sockaddr * )&sock, &tlen ) == -1 )
   {
     closed( d->auth_fd );
     d->auth_fd = -1;
     d->auth_state = 0;
     if ( d->atimes < 20 )
       d->atimes++;
     return;
   }

   serv = getservbyname( "ident", "tcp" );
   if ( !serv )
     sock.sin_port = htons( 113 );
   else
     sock.sin_port = serv->s_port;
   sock.sin_family = AF_INET;

   if ( connect( d->auth_fd, ( struct sockaddr *)&sock, sizeof(sock)) == -1
       && errno != EINPROGRESS )
   {
     /* Identd Denied */
/*     bug( "Unable to verify userid", 9 ); */
     closed( d->auth_fd );
     d->auth_fd = -1;
     d->auth_state = 0;
     if ( d->atimes < 20 )
       d->atimes++;
     return;
   }

   d->auth_inc = 0;
   d->abuf[0] = '\0';
   d->auth_state |= ( FLAG_WRAUTH|FLAG_AUTH ); /* Successful, but not sent */
   if ( d->auth_fd > maxdesc ) maxdesc = d->auth_fd;
   return;
}

/* send_auth */

void  send_auth( struct descriptor_data *d )
{
    struct  sockaddr_in  us, them;
    char    authbuf[32];
    socklen_t ulen, tlen;
    size_t  len, sent = 0;
    ssize_t n;

    if ( !d || d->auth_fd < 0 || d->descriptor < 0 )
      return;

    memset( &us, 0, sizeof( us ) );
    memset( &them, 0, sizeof( them ) );
    tlen = ulen = sizeof( us );

    if ( getsockname( d->descriptor, ( struct sockaddr *)&us, &ulen ) != 0
    ||   getpeername( d->descriptor, ( struct sockaddr *)&them, &tlen ) != 0 )
    {
	bug( "auth getsockname error" );
	goto authsenderr;
    }

    /* compose request */
    SPRINTF( authbuf, "%u , %u\r\n",
	(unsigned int)ntohs(them.sin_port),
	(unsigned int)ntohs(us.sin_port));

    len = strlen( authbuf );
    while ( sent < len )
    {
	n = write( d->auth_fd, authbuf + sent, len - sent );
	if ( n < 0 )
	{
	    if ( errno == EINTR )
		continue;
	    if ( ( errno == EWOULDBLOCK || errno == EAGAIN ) && sent == 0 )
		return;
	    break;
	}
	if ( n == 0 )
	    break;
	sent += static_cast<size_t>( n );
    }

    if ( sent < len )
    {
	if ( d->atimes >= 19 )
	  perror( "send_auth" );
	authsenderr:
	if ( d->auth_fd >= 0 )
	{
	    closed( d->auth_fd );
	    if ( d->auth_fd == maxdesc ) maxdesc--;
	    d->auth_fd = -1;
	}
	d->auth_state &= ~( FLAG_AUTH | FLAG_WRAUTH ); /* Failure/Continue */
	d->auth_inc = 0;
	if ( d->atimes < 20 ) d->atimes++;
	return;
    }
    d->auth_state &= ~FLAG_WRAUTH;  /* Successfully sent request */
    return;
}

/* read_auth */
void  read_auth( struct descriptor_data *d )
{
    char     *s, *t;
    int      len;                    /* length read */
    char     ruser[20], system[8];   /* remote userid */
    u_short  remp = 0, locp = 0;     /* remote port, local port */
    int      avail;

    if ( !d || d->auth_fd < 0 )
      return;

   *system = *ruser = '\0';

   /*
    * Can't allow any other reads from client fd while waiting on the
    * authfd to return a full valid string.  Use the client's input buffer
    * to buffer the authd reply.  May take more than one read.
    */

    avail = static_cast<int>( sizeof( d->abuf ) ) - 1 - d->auth_inc;
    len = ( avail > 0 ) ? readd( d->auth_fd, d->abuf + d->auth_inc, avail ) : 0;

    if ( len > 0 )
    {
	d->auth_inc += len;
	d->abuf[d->auth_inc] = '\0';
    }
    else if ( len < 0 )
    {
	if ( errno == EWOULDBLOCK || errno == EAGAIN || errno == EINTR )
	    return;
    }

    if ( ( len > 0 ) && ( d->auth_inc < static_cast<int>( sizeof( d->abuf ) ) - 1 )
    &&   ( sscanf( d->abuf, "%hu , %hu : USERID : %*[^:]: %19s",
          &remp, &locp, ruser ) == 3 ) )
    {
	s = strrchr( d->abuf, ':' );
	if ( s )
	{
	    *s++ = '\0';
	    char *colon2 = strrchr( d->abuf, ':' );
	    if ( colon2 )
	    {
		for ( t = colon2 + 1; *t; t++ )
		  if ( !isspace( static_cast<unsigned char>( *t ) ) )
		    break;
		snprintf( system, sizeof( system ), "%s", t );
		(void)system;
	    }

	    for ( t = ruser; *s && ( t < ruser + sizeof( ruser ) - 1 ); s++ )
	      if ( !isspace( static_cast<unsigned char>( *s ) ) && *s != ':' )
		*t++ = *s;
	    *t = '\0';
	    smash_tilde( ruser );

	    log_printf_plus( LOG_COMM, LEVEL_GOD, "auth reply ok, incoming user: [%s]", ruser );
	}
	else
	{
	    ruser[0] = '\0';
	}
    }
    else if ( len > 0 )
    {
	if ( !strchr( d->abuf, '\n' ) && !strchr( d->abuf, '\r' )
	&&   d->auth_inc < static_cast<int>( sizeof( d->abuf ) ) - 1 )
	    return;
	log_printf_plus( LOG_COMM, LEVEL_GOD, "bad auth reply: %s", d->abuf );
	ruser[0] = '\0';
    }
    closed( d->auth_fd );
    if ( d->auth_fd == maxdesc )
      --maxdesc;
    d->auth_inc = 0;
    *d->abuf = '\0';
    d->auth_fd = -1;
    d->auth_state = 0;

    if ( ruser[0] == '\0' )
      SPRINTF( ruser, "(no auth)" );

    if ( d->user )
        STRFREE( d->user );
    d->user = STRALLOC( ruser );
    return;
}

static bool nonblock( int s )
{
  int flags;

  if ( s < 0 )
    return false;

  flags = fcntl( s, F_GETFL, 0 );
  if ( flags == -1 )
  {
    perror( "Noblock: F_GETFL" );
    return false;
  }

  if ( fcntl( s, F_SETFL, flags | O_NONBLOCK ) == -1 )
  {
    perror( "Noblock: F_SETFL" );
    return false;
  }

  return true;
}

/*
int workaround( struct descriptor_data *d )
{
   BAN_DATA *tmp;
   char *nicnamehost;
   char buf[MAX_STRING_LENGTH];
   
   SPRINTF( buf, "%s!%s@%s", d->name, d->user, d->host );
   nicknamehost = str_dup( buf );
   for ( tmp = ncsa_list; tmp; tmp = tmp->next )
   {
     if ( !match( tmp->user, nicknamehost ) )
     {
       FREE( nicknamehost );
       write_to_buffer( d, "NCSA telnet patch loaded.\n" );
       return 1;
     }
   }
   FREE(nicknamehost);
   return 0;
}
*/
