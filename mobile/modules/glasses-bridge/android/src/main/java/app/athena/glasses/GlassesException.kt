package app.athena.glasses

import expo.modules.kotlin.exception.CodedException

/** Surfaces as a JS Error with code ERR_GLASSES and the toolkit's own description as the message. */
class GlassesException(message: String, cause: Throwable? = null) : CodedException("ERR_GLASSES", message, cause)
