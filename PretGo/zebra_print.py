"""
==============================================
  MODULE ZEBRA — Impression série ZPL
  (optionnel — nécessite pyserial)
==============================================
Ce module gère l'envoi de commandes ZPL à une
imprimante Zebra via port série (COM/ttyS).

Installation :  pip install pyserial
"""

import time


def envoyer_zpl(port, baud, zpl_commands, timeout=5):
    """
    Envoyer une liste de commandes ZPL à l'imprimante Zebra
    via le port série spécifié.

    Args:
        port (str): Port série (ex: 'COM3', '/dev/ttyS4')
        baud (int): Débit en bauds (ex: 38400)
        zpl_commands (list): Liste de chaînes ZPL à envoyer
        timeout (int): Timeout de connexion en secondes

    Returns:
        dict: {'success': True/False, 'error': str si échec,
               'printed': int nombre d'étiquettes imprimées}
    """
    try:
        import serial
    except ImportError:
        return {
            'success': False,
            'error': 'Le module pyserial n\'est pas installé. '
                     'Exécutez : pip install pyserial',
            'printed': 0
        }

    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout
        )

        printed = 0
        for zpl in zpl_commands:
            ser.write(zpl.encode('utf-8'))
            ser.flush()
            printed += 1
            # Court délai entre les étiquettes pour ne pas saturer le buffer
            if len(zpl_commands) > 1:
                time.sleep(0.3)

        ser.close()
        return {'success': True, 'printed': printed}

    except serial.SerialException as e:
        return {
            'success': False,
            'error': f'Erreur port série ({port}) : {str(e)}',
            'printed': 0
        }
    except Exception as e:
        return {
            'success': False,
            'error': f'Erreur inattendue : {str(e)}',
            'printed': 0
        }


def tester_connexion(port, baud, timeout=3):
    """
    Tester la connexion au port série de l'imprimante Zebra.

    Returns:
        dict: {'success': True/False, 'error': str si échec}
    """
    try:
        import serial
    except ImportError:
        return {
            'success': False,
            'error': 'Le module pyserial n\'est pas installé.'
        }

    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            timeout=timeout
        )
        # Envoyer une commande de statut Zebra
        ser.write(b'~HS')
        ser.flush()
        time.sleep(0.5)
        # Lire la réponse (si disponible)
        response = ser.read(ser.in_waiting) if ser.in_waiting else b''
        ser.close()
        return {
            'success': True,
            'response': response.decode('utf-8', errors='replace')
        }
    except serial.SerialException as e:
        return {
            'success': False,
            'error': f'Impossible de se connecter au port {port} : {str(e)}'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }
